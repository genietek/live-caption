"""Application controller: owns settings, models, downloads and the engine,
and turns background-thread events into Qt signals for the pages.

Nothing slow runs on the GUI thread: hardware detection, model loading,
downloads and update checks all run on worker threads.
"""

from __future__ import annotations

import gc
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from .. import catalog, config, downloads, engine, hardware

log = logging.getLogger(__name__)


class Controller(QObject):
    models_changed = Signal()
    download_changed = Signal(object)          # downloads.DownloadStatus
    engine_state = Signal(str, str)            # state, message
    caption = Signal(dict)
    status = Signal(str, bool)                 # message, is_error
    metrics = Signal(object)
    language_changed = Signal(object)          # "en" | "es" | None (other language)
    hardware_ready = Signal(object)
    gpu_failed = Signal(str, str)              # model id, message
    info = Signal(str)                         # one-off notices (import results, updates)
    setting_changed = Signal(str, object)      # keeps duplicate controls in sync

    _bridge = Signal(str, object)              # worker threads -> GUI thread

    def __init__(self, settings: config.Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.hw: hardware.HardwareInfo | None = None
        self.scan = catalog.ScanResult()
        self.active: engine.LoadedModel | None = None
        self.transcriber: engine.Transcriber | None = None
        self.state = "idle"     # idle, loading, ready, running, error
        self.loading_model: str | None = None
        self.failed: dict[str, str] = {}
        self.updates: set[str] = set()
        self._start_after_load = False
        self._bridge.connect(self._on_bridge)
        self.downloads = self._make_download_manager()

    # -- setup ------------------------------------------------------------------

    def _make_download_manager(self) -> downloads.DownloadManager:
        return downloads.DownloadManager(
            self.model_dir, lambda st: self._bridge.emit("download", st))

    @property
    def model_dir(self) -> Path:
        return self.settings.resolved_model_dir()

    def start_up(self) -> None:
        catalog.cleanup_trash(self.model_dir)
        self.rescan()
        threading.Thread(target=lambda: self._bridge.emit("hardware", hardware.detect()),
                         name="HardwareDetect", daemon=True).start()

    def set_setting(self, name: str, value) -> None:
        setattr(self.settings, name, value)
        self.save()
        self.setting_changed.emit(name, value)

    def save(self) -> None:
        try:
            config.save_settings(self.settings)
        except OSError as exc:
            self.status.emit(f"Could not save settings: {exc}", True)

    # -- models -----------------------------------------------------------------

    def rescan(self) -> None:
        self.scan = catalog.scan(self.model_dir, self.settings.imported_models)
        self.models_changed.emit()

    def recommended(self) -> str | None:
        if self.hw is None:
            return None
        return catalog.recommend(self.hw.has_nvidia_gpu, self.hw.vram_total_mb,
                                 self.hw.ram_total_gb, self.hw.logical_cores)

    def model_state(self, model_id: str) -> tuple[str, str]:
        """(state, detail) for display: Downloading, Verifying, Loading, Ready,
        Installed, Update available, Failed, Not downloaded."""
        st = self.downloads.status(model_id)
        if st and st.state in ("queued", "downloading"):
            return "Downloading", st.message
        if st and st.state == "verifying":
            return "Verifying", st.message
        if self.loading_model == model_id:
            return "Loading", ""
        if self.active and self.active.model_id == model_id:
            return "Ready", f"{self.active.device.upper()} {self.active.compute_type}"
        if model_id in self.failed:
            return "Failed", self.failed[model_id]
        if model_id in self.scan.installed:
            if model_id in self.updates:
                return "Update available", ""
            return "Installed", ""
        if st and st.state == "failed":
            return "Failed", st.message
        if model_id in self.scan.invalid:
            return "Not downloaded", f"Found but incomplete: {self.scan.invalid[model_id]}"
        return "Not downloaded", ""

    def set_model_dir(self, path: str) -> str | None:
        if any(self.downloads.is_active(m.id) for m in catalog.CATALOG):
            return "Wait for downloads to finish before changing the model folder."
        try:
            Path(path or config.default_model_dir()).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"Cannot use that folder: {exc}"
        self.downloads.shutdown(0.5)
        self.settings.model_dir = path
        self.save()
        self.downloads = self._make_download_manager()
        self.rescan()
        return None

    def download(self, model_id: str) -> None:
        self.failed.pop(model_id, None)
        if not self.downloads.request(model_id):
            self.status.emit(f"{catalog.get(model_id).name} is already downloading.", False)
        self.models_changed.emit()

    def cancel_download(self, model_id: str) -> None:
        self.downloads.cancel(model_id)

    def delete_model(self, model_id: str) -> str | None:
        if (self.active and self.active.model_id == model_id) or self.loading_model == model_id:
            return "This model is in use. Select another model first."
        if self.downloads.is_active(model_id):
            return "This model is downloading."
        inst = self.scan.installed.get(model_id)
        if inst is None:
            return None
        if inst.managed:
            try:
                catalog.delete_managed_model(self.model_dir, model_id)
            except OSError as exc:
                return f"Could not delete the model: {exc}"
        else:
            # Imported folders belong to the user; only forget them.
            self.settings.imported_models.pop(model_id, None)
            self.save()
        self.updates.discard(model_id)
        self.rescan()
        return None

    def import_existing(self) -> None:
        def work():
            roots = [self.model_dir, *catalog.LEGACY_MODEL_ROOTS, *catalog.hf_cache_roots()]
            self._bridge.emit("imported", catalog.find_existing_models(roots))
        threading.Thread(target=work, name="ImportModels", daemon=True).start()

    def check_updates(self) -> None:
        installed = {mid: m.revision for mid, m in self.scan.installed.items() if m.managed}

        def work():
            found, unknown, errors = set(), [], []
            for mid, rev in installed.items():
                try:
                    res = downloads.check_update(mid, rev)
                except Exception as exc:
                    errors.append(downloads.describe_error(exc))
                    continue
                if res is None:
                    unknown.append(mid)
                elif res:
                    found.add(mid)
            self._bridge.emit("updates", (found, unknown, errors))
        threading.Thread(target=work, name="UpdateCheck", daemon=True).start()

    # -- engine -----------------------------------------------------------------

    def select_model(self, model_id: str, device: str | None = None) -> None:
        inst = self.scan.installed.get(model_id)
        if inst is None:
            if self.settings.auto_download:
                self.download(model_id)
            else:
                self.status.emit(f"{catalog.get(model_id).name} is not downloaded yet.", True)
            return
        if self.loading_model:
            self.status.emit("A model is already loading.", False)
            return
        was_running = self.transcriber is not None
        self._stop_transcriber()
        self._unload()
        self.loading_model = model_id
        self.failed.pop(model_id, None)
        self._set_state("loading", f"Loading {inst.info.name}...")
        self.models_changed.emit()
        dev = device or self.settings.device
        ct = self.settings.compute_type

        def work():
            try:
                loaded = engine.load_model(model_id, inst.path, dev, ct)
                self._bridge.emit("loaded", (loaded, was_running))
            except engine.GpuUnavailable as exc:
                self._bridge.emit("gpu_failed", (model_id, str(exc), was_running))
            except Exception as exc:
                log.exception("Model load failed")
                self._bridge.emit("load_failed", (model_id, str(exc)))
        threading.Thread(target=work, name="ModelLoad", daemon=True).start()

    def _unload(self) -> None:
        if self.active is not None:
            self.active = None
            gc.collect()   # releases CTranslate2 weights (and GPU memory)

    def _stream_options(self) -> engine.StreamOptions:
        s = self.settings
        return engine.StreamOptions(
            language=s.language, redetect_seconds=s.redetect_seconds,
            chunk_seconds=s.chunk_seconds, overlap_seconds=s.overlap_seconds,
            silence_threshold=s.silence_threshold, max_backlog_seconds=s.max_backlog_seconds,
            beam_size=s.beam_size, audio_source=s.audio_source, audio_device=s.audio_device,
            preferred_device_name=s.preferred_device_name)

    def start(self) -> None:
        if self.transcriber is not None:
            return
        if self.active is None:
            mid = self.settings.default_model
            if mid not in self.scan.installed:
                self.status.emit("Download and select a model on the Models page first.", True)
                return
            self._start_after_load = True
            self.select_model(mid)
            return
        self.transcriber = engine.Transcriber(
            self.active, self._stream_options(), lambda k, v: self._bridge.emit(k, v))
        self.transcriber.start()
        self._set_state("running", "Listening")

    def stop(self) -> None:
        self._start_after_load = False
        self._stop_transcriber()
        if self.active is not None:
            self._set_state("ready", "Stopped")

    def toggle(self) -> None:
        if self.transcriber is not None or self._start_after_load:
            self.stop()
        else:
            self.start()

    def set_paused(self, paused: bool) -> None:
        if self.transcriber is not None:
            (self.transcriber.paused.set if paused else self.transcriber.paused.clear)()
            self._set_state("running", "Paused" if paused else "Listening")

    def set_language(self, lang: str) -> None:
        if lang == self.settings.language:
            return
        self.set_setting("language", lang)
        if self.transcriber is not None:
            self.transcriber.language = engine.LanguageTracker(lang, self.settings.redetect_seconds)
        self.language_changed.emit(None if lang == "auto" else lang)

    def redetect_language(self) -> None:
        if self.transcriber is not None:
            self.transcriber.redetect_language()

    def _stop_transcriber(self) -> None:
        t, self.transcriber = self.transcriber, None
        if t is not None:
            t.stop()

    def _set_state(self, state: str, message: str = "") -> None:
        self.state = state
        self.engine_state.emit(state, message)

    def shutdown(self) -> None:
        self._stop_transcriber()
        self.downloads.shutdown()
        self._unload()

    # -- background events --------------------------------------------------------

    @Slot(str, object)
    def _on_bridge(self, kind: str, value) -> None:
        if kind == "download":
            self.download_changed.emit(value)
            if value.state in ("done", "failed", "cancelled"):
                self.rescan()
                if value.state == "done":
                    self.updates.discard(value.model_id)
                    self.status.emit(f"{catalog.get(value.model_id).name} installed. "
                                     "Press Select on the Models page to use it.", False)
            else:
                self.models_changed.emit()
        elif kind == "hardware":
            self.hw = value
            self.hardware_ready.emit(value)
            self.models_changed.emit()
        elif kind == "loaded":
            loaded, was_running = value
            self.loading_model = None
            self.active = loaded
            self.settings.default_model = loaded.model_id
            self.save()
            self._set_state("ready", f"{catalog.get(loaded.model_id).name} ready "
                                     f"({loaded.device.upper()}, {loaded.compute_type}, "
                                     f"loaded in {loaded.load_seconds:.1f}s)")
            if loaded.note:
                self.status.emit(loaded.note, True)
            self.models_changed.emit()
            if was_running or self._start_after_load:
                self._start_after_load = False
                self.start()
        elif kind == "gpu_failed":
            model_id, msg, _ = value
            self.loading_model = None
            self._start_after_load = False
            self._set_state("error", msg)
            self.models_changed.emit()
            self.gpu_failed.emit(model_id, msg)
        elif kind == "load_failed":
            model_id, msg = value
            self.loading_model = None
            self._start_after_load = False
            self.failed[model_id] = msg
            self._set_state("error", f"Could not load {catalog.get(model_id).name}: {msg}")
            self.models_changed.emit()
        elif kind == "imported":
            found, notes = value
            added = []
            for mid, path in found.items():
                if mid not in self.scan.installed:
                    self.settings.imported_models[mid] = str(path)
                    added.append(catalog.get(mid).name)
            self.save()
            self.rescan()
            lines = [f"Imported: {', '.join(added)}." if added else "No new models found."]
            self.info.emit("\n".join(lines + notes))
        elif kind == "updates":
            found, unknown, errors = value
            self.updates = found
            self.models_changed.emit()
            msg = (f"Updates available: {', '.join(catalog.get(m).name for m in found)}."
                   if found else "All downloaded models are up to date.")
            if unknown:
                msg += " Imported models cannot be checked."
            if errors:
                msg = errors[0]
            self.info.emit(msg)
        elif kind == "caption":
            self.caption.emit(value)
        elif kind == "metrics":
            self.metrics.emit(value)
        elif kind == "language":
            self.language_changed.emit(value if value else "other")
        elif kind == "audio":
            self.status.emit(*value)
        elif kind == "status":
            self.status.emit(value, False)
        elif kind == "error":
            self.status.emit(value, True)
        elif kind == "stopped":
            if value is self.transcriber:      # ended on its own (not via stop())
                err = value.error
                self.transcriber = None
                self._set_state("error" if err else "ready",
                                f"Stopped: {err}" if err else "Stopped")
