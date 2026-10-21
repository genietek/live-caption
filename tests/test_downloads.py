import hashlib
import io
import threading
import time
import urllib.error

import pytest

from livecaption import catalog, downloads

TINY = catalog.get("tiny")
MODEL_BIN = b"\x01" * (TINY.params_m * 1_000_000)


def remote_files():
    files = {
        "config.json": b"{}",
        "tokenizer.json": b"{}",
        "vocabulary.txt": b"a\nb\n",
        "model.bin": MODEL_BIN,
    }
    listing = []
    for name, data in files.items():
        if name == "model.bin":
            listing.append(downloads.RemoteFile(name, len(data), sha256=hashlib.sha256(data).hexdigest()))
        else:
            sha1 = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            listing.append(downloads.RemoteFile(name, len(data), git_sha1=sha1))
    return files, listing


class FakeResponse(io.BytesIO):
    def __init__(self, data, status, fail_after=None):
        super().__init__(data)
        self.status = status
        self.fail_after = fail_after

    def read(self, n=-1):
        if self.fail_after is not None and self.tell() >= self.fail_after:
            raise ConnectionError("connection reset")
        return super().read(n)


class FakeHub:
    """Serves files from memory with HTTP Range support and optional failures."""

    def __init__(self, files, listing, fail_after=None, slow=0.0):
        self.files, self.listing = files, listing
        self.fail_after = fail_after
        self.slow = slow
        self.requests = []

    def fetch(self, repo):
        return downloads.RemoteModel(repo, "rev1", list(self.listing))

    def url_for(self, repo, revision, name):
        return name

    def open(self, url, start=0):
        self.requests.append((url, start))
        if self.slow:
            time.sleep(self.slow)
        data = self.files[url]
        fail = self.fail_after if url == "model.bin" else None
        if fail is not None:
            self.fail_after = None   # fail once
        return FakeResponse(data[start:], 206 if start else 200,
                            None if fail is None else max(0, fail - start))


def run(manager, model_id="tiny", timeout=30):
    events = []
    done = threading.Event()

    def listener(st):
        events.append(st)
        if st.state in ("done", "failed", "cancelled"):
            done.set()
    manager._listener = listener
    assert manager.request(model_id)
    assert done.wait(timeout)
    return events


def make_manager(tmp_path, hub):
    return downloads.DownloadManager(tmp_path / "models", lambda st: None, fetch=hub.fetch,
                                     opener=hub.open, url_for=hub.url_for)


def test_download_completes_and_installs(tmp_path):
    hub = FakeHub(*remote_files())
    mgr = make_manager(tmp_path, hub)
    events = run(mgr)
    assert events[-1].state == "done", events[-1].message
    states = [e.state for e in events]
    assert "downloading" in states and "verifying" in states
    assert events[-1].total == sum(len(d) for d in hub.files.values())
    result = catalog.scan(tmp_path / "models")
    assert "tiny" in result.installed
    assert result.installed["tiny"].revision == "rev1"
    assert not (tmp_path / "models" / ".partial" / "tiny").exists()


def test_interrupted_download_resumes_with_range(tmp_path):
    hub = FakeHub(*remote_files(), fail_after=len(MODEL_BIN) // 2)
    mgr = make_manager(tmp_path, hub)
    first = run(mgr)
    assert first[-1].state == "failed"
    assert "tiny" not in catalog.scan(tmp_path / "models").installed
    partial = tmp_path / "models" / ".partial" / "tiny" / "model.bin"
    have = partial.stat().st_size
    assert 0 < have < len(MODEL_BIN)
    second = run(mgr)
    assert second[-1].state == "done", second[-1].message
    assert ("model.bin", have) in hub.requests          # resumed, not restarted
    assert "tiny" in catalog.scan(tmp_path / "models").installed


def test_corrupt_file_fails_verification(tmp_path):
    files, listing = remote_files()
    files["model.bin"] = b"\x02" * len(MODEL_BIN)        # same size, wrong content
    mgr = make_manager(tmp_path, FakeHub(files, listing))
    events = run(mgr)
    assert events[-1].state == "failed"
    assert "verification" in events[-1].message
    assert not (tmp_path / "models" / ".partial" / "tiny" / "model.bin").exists()
    assert "tiny" not in catalog.scan(tmp_path / "models").installed


def test_duplicate_requests_are_ignored(tmp_path):
    hub = FakeHub(*remote_files(), slow=0.3)
    mgr = make_manager(tmp_path, hub)
    done = threading.Event()
    mgr._listener = lambda st: done.set() if st.state == "done" else None
    assert mgr.request("tiny")
    assert not mgr.request("tiny")
    assert done.wait(30)
    assert len([r for r in hub.requests if r[0] == "model.bin"]) == 1


def test_cancel_keeps_partial_files(tmp_path):
    hub = FakeHub(*remote_files())
    original_open = hub.open
    mgr = make_manager(tmp_path, hub)

    def cancelling_open(url, start=0):
        resp = original_open(url, start)
        if url == "model.bin":
            mgr.cancel("tiny")
        return resp
    mgr._open = cancelling_open
    events = run(mgr)
    assert events[-1].state == "cancelled"
    assert "tiny" not in catalog.scan(tmp_path / "models").installed
    assert mgr.request("tiny")       # can be retried after cancelling
    mgr.shutdown()


def test_network_errors_are_readable():
    msg = downloads.describe_error(urllib.error.URLError("timed out"))
    assert "Network problem" in msg and "Retry" in msg
    assert "404" in downloads.describe_error(urllib.error.HTTPError("u", 404, "nf", {}, None))


def test_only_model_files_are_wanted():
    assert downloads.wanted("model.bin") and downloads.wanted("vocabulary.json")
    assert not downloads.wanted("README.md") and not downloads.wanted(".gitattributes")


def test_update_check_needs_manifest():
    hub = FakeHub(*remote_files())
    assert downloads.check_update("tiny", None, hub.fetch) is None
    assert downloads.check_update("tiny", "rev1", hub.fetch) is False
    assert downloads.check_update("tiny", "old", hub.fetch) is True


def test_unknown_model_is_rejected(tmp_path):
    mgr = make_manager(tmp_path, FakeHub(*remote_files()))
    with pytest.raises(KeyError):
        mgr.request("tiny.en")
