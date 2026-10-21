"""Model download manager.

Files are listed (with sizes and hashes) through the Hugging Face Hub API, then
streamed directly so the app can show byte progress and speed, cancel between
blocks and resume with HTTP Range requests. Each file is verified (SHA-256 for
LFS files, git blob SHA-1 for small files) before the model folder is moved
into place in one rename, so a model is never half-installed.

Downloads run one at a time on a background thread; the UI only receives
callbacks. Nothing here touches Qt.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import queue
import shutil
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import catalog

log = logging.getLogger(__name__)

BLOCK = 1024 * 1024
USER_AGENT = "LiveCaption/0.1 (+faster-whisper model downloader)"


class Cancelled(Exception):
    pass


@dataclass
class RemoteFile:
    name: str
    size: int | None
    sha256: str | None = None      # LFS files
    git_sha1: str | None = None    # small non-LFS files (git blob id)


@dataclass
class RemoteModel:
    repo: str
    revision: str
    files: list[RemoteFile]

    @property
    def total_size(self) -> int | None:
        if any(f.size is None for f in self.files):
            return None
        return sum(f.size for f in self.files)


def wanted(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in catalog.DOWNLOAD_PATTERNS)


def fetch_remote_model(repo: str) -> RemoteModel:
    """Ask the Hub which files a repo has at its current revision."""
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo, files_metadata=True, timeout=30)
    files = []
    for s in info.siblings or []:
        if not wanted(s.rfilename):
            continue
        lfs = s.lfs
        sha256 = (lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)) if lfs else None
        files.append(RemoteFile(s.rfilename, s.size, sha256, None if lfs else s.blob_id))
    if not any(f.name == "model.bin" for f in files):
        raise RuntimeError(f"{repo} has no model.bin; not a CTranslate2 model")
    return RemoteModel(repo, info.sha, files)


def file_url(repo: str, revision: str, name: str) -> str:
    from huggingface_hub import hf_hub_url

    return hf_hub_url(repo, name, revision=revision)


def open_url(url: str, start: int = 0, timeout: float = 30):
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if start:
        headers["Range"] = f"bytes={start}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def hash_file(path: Path, algo: str, prefix: bytes = b"", cancel: threading.Event | None = None) -> str:
    h = hashlib.new(algo)
    h.update(prefix)
    with open(path, "rb") as f:
        while chunk := f.read(BLOCK):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            h.update(chunk)
    return h.hexdigest()


def verify_file(path: Path, rf: RemoteFile, cancel: threading.Event | None = None) -> bool:
    size = path.stat().st_size
    if rf.size is not None and size != rf.size:
        return False
    if rf.sha256:
        return hash_file(path, "sha256", cancel=cancel) == rf.sha256
    if rf.git_sha1:
        return hash_file(path, "sha1", f"blob {size}\0".encode(), cancel) == rf.git_sha1
    return True


@dataclass
class DownloadStatus:
    model_id: str
    state: str = "queued"     # queued, downloading, verifying, done, failed, cancelled
    done: int = 0
    total: int | None = None
    speed: float = 0.0        # bytes per second
    message: str = ""
    started: float = field(default_factory=time.monotonic)


Listener = Callable[[DownloadStatus], None]


class DownloadManager:
    """Queue of model downloads processed on one worker thread."""

    def __init__(self, model_dir: Path, listener: Listener,
                 fetch: Callable[[str], RemoteModel] = fetch_remote_model,
                 opener: Callable = open_url,
                 url_for: Callable[[str, str, str], str] = file_url):
        self.model_dir = Path(model_dir)
        self._listener = listener
        self._fetch = fetch
        self._open = opener
        self._url_for = url_for
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._status: dict[str, DownloadStatus] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = False

    # -- public API (any thread) --------------------------------------------

    def is_active(self, model_id: str) -> bool:
        with self._lock:
            st = self._status.get(model_id)
            return st is not None and st.state in ("queued", "downloading", "verifying")

    def status(self, model_id: str) -> DownloadStatus | None:
        with self._lock:
            return self._status.get(model_id)

    def request(self, model_id: str) -> bool:
        """Queue a download. Returns False if it is already queued or running."""
        catalog.get(model_id)
        with self._lock:
            if self._stopping:
                return False
            st = self._status.get(model_id)
            if st is not None and st.state in ("queued", "downloading", "verifying"):
                return False
            self._status[model_id] = DownloadStatus(model_id)
            self._cancel[model_id] = threading.Event()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="ModelDownloads", daemon=True)
                self._thread.start()
        self._queue.put(model_id)
        self._notify(model_id)
        return True

    def cancel(self, model_id: str) -> None:
        with self._lock:
            ev = self._cancel.get(model_id)
        if ev is not None:
            ev.set()

    def shutdown(self, timeout: float = 3.0) -> None:
        with self._lock:
            self._stopping = True
            for ev in self._cancel.values():
                ev.set()
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout)

    # -- worker ---------------------------------------------------------------

    def _notify(self, model_id: str) -> None:
        with self._lock:
            st = self._status.get(model_id)
            snapshot = DownloadStatus(**vars(st)) if st else None
        if snapshot is not None:
            try:
                self._listener(snapshot)
            except Exception:  # never let a UI callback kill the worker
                log.exception("Download listener failed")

    def _update(self, model_id: str, **changes) -> None:
        with self._lock:
            st = self._status[model_id]
            for k, v in changes.items():
                setattr(st, k, v)
        self._notify(model_id)

    def _run(self) -> None:
        while True:
            model_id = self._queue.get()
            if model_id is None:
                return
            cancel = self._cancel[model_id]
            try:
                if cancel.is_set():
                    raise Cancelled()
                self._download(model_id, cancel)
                self._update(model_id, state="done", message="Installed")
            except Cancelled:
                self._update(model_id, state="cancelled", speed=0.0,
                             message="Cancelled. Partial files are kept so the download can resume.")
            except Exception as exc:  # report every failure to the UI
                log.exception("Download of %s failed", model_id)
                self._update(model_id, state="failed", speed=0.0, message=describe_error(exc))

    def _download(self, model_id: str, cancel: threading.Event) -> None:
        info = catalog.get(model_id)
        self._update(model_id, state="downloading", message="Contacting Hugging Face...")
        remote = self._fetch(info.repo)
        total = remote.total_size
        partial = self.model_dir / catalog.PARTIAL_DIR / model_id
        partial.mkdir(parents=True, exist_ok=True)
        if total is not None:
            needed = total - sum((partial / f.name).stat().st_size
                                 for f in remote.files if (partial / f.name).is_file())
            free = shutil.disk_usage(partial).free
            if needed > free:
                raise OSError(f"Not enough disk space: need {needed / 1e9:.1f} GB, "
                              f"{free / 1e9:.1f} GB free in {self.model_dir}")

        done = 0
        speed = SpeedMeter()
        self._update(model_id, total=total, done=0, message="Downloading")
        for rf in remote.files:
            dest = partial / rf.name
            have = dest.stat().st_size if dest.is_file() else 0
            if rf.size is not None and have > rf.size:
                dest.unlink()
                have = 0
            if rf.size is None or have < rf.size:
                have = self._fetch_file(model_id, remote, rf, dest, have, done, speed, cancel)
            done += have

        self._update(model_id, state="verifying", speed=0.0, message="Verifying files")
        for rf in remote.files:
            dest = partial / rf.name
            if not verify_file(dest, rf, cancel):
                dest.unlink(missing_ok=True)   # so a retry downloads it again
                raise RuntimeError(f"{rf.name} failed verification and was removed; press Retry")

        manifest = {
            "model_id": model_id, "repo": remote.repo, "revision": remote.revision,
            "installed": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "files": {f.name: {"size": f.size, "sha256": f.sha256} for f in remote.files},
        }
        (partial / catalog.MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        ok, reason = catalog.validate_model_dir(partial, info)
        if not ok:
            raise RuntimeError(f"Downloaded model is incomplete: {reason}")

        final = self.model_dir / model_id
        if final.exists():   # replacing an older or broken copy
            old = self.model_dir / catalog.TRASH_DIR / f"{model_id}-{int(time.time() * 1000)}"
            old.parent.mkdir(exist_ok=True)
            os.replace(final, old)
            shutil.rmtree(old, ignore_errors=True)
        os.replace(partial, final)

    def _fetch_file(self, model_id, remote, rf, dest, have, done_before, speed, cancel) -> int:
        url = self._url_for(remote.repo, remote.revision, rf.name)
        for attempt in range(4):
            try:
                resp = self._open(url, have)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 416 and have:      # range not satisfiable: start over
                    dest.unlink(missing_ok=True)
                    have = 0
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 3:
                    raise
                if cancel.wait(2 ** attempt):
                    raise Cancelled()
        with resp:
            status = getattr(resp, "status", 200)
            if have and status != 206:            # server ignored Range
                have = 0
            last_ui = 0.0
            with open(dest, "ab" if have else "wb") as out:
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    chunk = resp.read(BLOCK)
                    if not chunk:
                        break
                    out.write(chunk)
                    have += len(chunk)
                    speed.add(len(chunk))
                    now = time.monotonic()
                    if now - last_ui >= 0.2:
                        last_ui = now
                        self._update(model_id, done=done_before + have, speed=speed.rate(),
                                     message=f"Downloading {rf.name}")
        if rf.size is not None and have != rf.size:
            raise ConnectionError(f"{rf.name}: connection closed early ({have} of {rf.size} bytes); "
                                  "press Retry to resume")
        return have


class SpeedMeter:
    """Download speed over the last few seconds."""

    def __init__(self, window: float = 3.0):
        self.window = window
        self.samples: list[tuple[float, int]] = []

    def add(self, n: int) -> None:
        now = time.monotonic()
        self.samples.append((now, n))
        while self.samples and now - self.samples[0][0] > self.window:
            self.samples.pop(0)

    def rate(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        span = self.samples[-1][0] - self.samples[0][0]
        return sum(n for _, n in self.samples[1:]) / span if span > 0 else 0.0


def describe_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return f"Access denied by the server (HTTP {exc.code})."
        if exc.code == 404:
            return "The model file was not found on Hugging Face (HTTP 404)."
        return f"Server error (HTTP {exc.code}). Try again later."
    if isinstance(exc, (urllib.error.URLError, ConnectionError, TimeoutError)):
        return f"Network problem: {getattr(exc, 'reason', exc)}. Check your connection and press Retry."
    name = type(exc).__name__
    if "OfflineMode" in name or "LocalEntryNotFound" in name or "Connect" in name:
        return "Could not reach Hugging Face. Check your connection and press Retry."
    return str(exc) or name


def check_update(model_id: str, installed_revision: str | None,
                 fetch: Callable[[str], RemoteModel] = fetch_remote_model) -> bool | None:
    """True/False when it can be determined, None when it cannot (no manifest)."""
    if not installed_revision:
        return None
    return fetch(catalog.get(model_id).repo).revision != installed_revision
