"""Controller and UI tests (Qt offscreen, model loading and downloads mocked)."""

import time

import pytest

pytest.importorskip("PySide6")

from livecaption import catalog, config, engine  # noqa: E402
from livecaption.ui.controller import Controller  # noqa: E402
from livecaption.ui.main_window import MainWindow  # noqa: E402

from .conftest import make_model  # noqa: E402


class FakeModel:
    def __init__(self, model_id):
        self.model_id = model_id


@pytest.fixture
def loads(monkeypatch):
    """Replace engine.load_model; records calls and can be told to fail."""
    calls = []
    behaviour = {}

    def fake_load(model_id, path, device="auto", compute="auto", factory=None):
        calls.append((model_id, device))
        time.sleep(0.05)
        err = behaviour.get((model_id, device)) or behaviour.get(model_id)
        if err:
            raise err
        return engine.LoadedModel(FakeModel(model_id), model_id, path, "cpu", "int8", 0.05)
    monkeypatch.setattr(engine, "load_model", fake_load)
    return calls, behaviour


@pytest.fixture
def app_with_models(qtbot):
    settings = config.load_settings()
    for mid in ("tiny", "small"):
        make_model(settings.resolved_model_dir() / mid, mid)
    ctl = Controller(settings)
    win = MainWindow(ctl)
    qtbot.addWidget(win)
    ctl.rescan()
    yield ctl, win
    ctl.shutdown()


def test_catalog_renders_all_models(app_with_models):
    ctl, win = app_with_models
    page = win.models
    assert set(page.cards) == {m.id for m in catalog.CATALOG}
    installed = [page.installed_box.itemAt(i).widget().info.id for i in range(page.installed_box.count())]
    available = [page.available_box.itemAt(i).widget().info.id for i in range(page.available_box.count())]
    assert installed == ["tiny", "small"]
    assert available == ["base", "medium", "large-v3", "large-v3-turbo"]
    tiny, medium = page.cards["tiny"], page.cards["medium"]
    assert not tiny.btn_select.isHidden() and tiny.btn_download.isHidden()
    assert not medium.btn_download.isHidden() and medium.btn_select.isHidden()
    assert medium.state.text() == "Not downloaded"


def test_model_switching_releases_old_model(qtbot, app_with_models, loads):
    ctl, win = app_with_models
    calls, _ = loads
    ctl.select_model("tiny")
    qtbot.waitUntil(lambda: ctl.active is not None)
    assert ctl.active.model_id == "tiny"
    assert ctl.model_state("tiny")[0] == "Ready"
    ctl.select_model("small")
    assert ctl.active is None                       # old model dropped before loading the next
    qtbot.waitUntil(lambda: ctl.active is not None)
    assert ctl.active.model_id == "small" and ctl.model_state("tiny")[0] == "Installed"
    assert ctl.settings.default_model == "small"
    assert config.load_settings().default_model == "small"   # persisted


def test_missing_model_is_not_loaded(qtbot, app_with_models, loads):
    ctl, win = app_with_models
    with qtbot.waitSignal(ctl.status) as blocker:
        ctl.select_model("medium")
    assert "not downloaded" in blocker.args[0]
    assert loads[0] == [] and ctl.downloads.status("medium") is None   # no download without consent


def test_failed_load_keeps_config(qtbot, app_with_models, loads):
    ctl, win = app_with_models
    _, behaviour = loads
    ctl.select_model("tiny")
    qtbot.waitUntil(lambda: ctl.active is not None)
    behaviour["small"] = RuntimeError("model.bin is corrupted")
    ctl.select_model("small")
    qtbot.waitUntil(lambda: ctl.loading_model is None)
    assert ctl.model_state("small") == ("Failed", "model.bin is corrupted")
    assert ctl.state == "error"
    assert config.load_settings().default_model == "tiny"


def test_gpu_failure_offers_cpu(qtbot, app_with_models, loads, monkeypatch):
    ctl, win = app_with_models
    calls, behaviour = loads
    behaviour[("tiny", "cuda")] = engine.GpuUnavailable("No CUDA device is visible")
    ctl.settings.device = "cuda"
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    ctl.select_model("tiny")
    qtbot.waitUntil(lambda: ctl.active is not None)
    assert calls == [("tiny", "cuda"), ("tiny", "cpu")]


def test_active_model_cannot_be_deleted(qtbot, app_with_models, loads):
    ctl, win = app_with_models
    ctl.select_model("tiny")
    qtbot.waitUntil(lambda: ctl.active is not None)
    assert "in use" in ctl.delete_model("tiny")
    assert win.models.cards["tiny"].btn_delete.isHidden()
    assert ctl.delete_model("small") is None
    assert "small" not in ctl.scan.installed
    assert not (ctl.model_dir / "small").exists()


def test_imported_model_is_only_forgotten(qtbot, tmp_path):
    ext = make_model(tmp_path / "hub" / "base", "base")
    settings = config.Settings(imported_models={"base": str(ext)})
    ctl = Controller(settings)
    ctl.rescan()
    assert not ctl.scan.installed["base"].managed
    assert ctl.delete_model("base") is None
    assert ext.exists() and "base" not in ctl.scan.installed
    ctl.shutdown()


def test_duplicate_download_request(qtbot, app_with_models, monkeypatch):
    ctl, win = app_with_models
    monkeypatch.setattr(ctl.downloads, "_fetch", lambda repo: time.sleep(1) or (_ for _ in ()).throw(OSError("x")))
    ctl.download("base")
    with qtbot.waitSignal(ctl.status) as blocker:
        ctl.download("base")
    assert "already downloading" in blocker.args[0]


def test_settings_persist_from_ui(qtbot, app_with_models):
    ctl, win = app_with_models
    win.dashboard.timestamps.setChecked(True)
    win.dashboard.lang_combo.setCurrentIndex(win.dashboard.lang_combo.findData("es"))
    s = config.load_settings()
    assert s.timestamps is True and s.language == "es"
    assert win.settings.lang.currentData() == "es"          # other page kept in sync
    assert win.settings._checks["timestamps"].isChecked()


def test_start_without_models_explains(qtbot):
    ctl = Controller(config.Settings())
    ctl.rescan()
    with qtbot.waitSignal(ctl.status) as blocker:
        ctl.start()
    assert "Download and select a model" in blocker.args[0]
    ctl.shutdown()


def test_offline_startup_makes_no_network_calls(qtbot, app_with_models, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network used"))
    ctl, win = app_with_models
    ctl.start_up()
    qtbot.waitUntil(lambda: ctl.hw is not None, timeout=10000)
    assert set(ctl.scan.installed) == {"tiny", "small"}


def test_captions_and_shutdown(qtbot, app_with_models, loads, monkeypatch):
    ctl, win = app_with_models

    class FakeTranscriber:
        def __init__(self, loaded, options, emit):
            self.emit, self.stopped = emit, False
            self.paused = __import__("threading").Event()

        def start(self):
            self.emit("caption", {"text": "Hola mundo", "language": "es", "time": 0})

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(engine, "Transcriber", FakeTranscriber)
    ctl.settings.default_model = "tiny"
    ctl.start()                                     # loads the default model, then starts
    qtbot.waitUntil(lambda: ctl.state == "running")
    qtbot.waitUntil(lambda: "Hola mundo" in win.dashboard.text.toPlainText())
    t = ctl.transcriber
    win.quit()
    assert t.stopped and ctl.transcriber is None and ctl.active is None
