"""Settings and About pages. Settings save as soon as they change."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .. import DISPLAY_NAME, __version__, audio, catalog, config, winapi
from ..engine import CPU_COMPUTE_TYPES, GPU_COMPUTE_TYPES, LANGUAGE_NAMES


class SettingsPage(QWidget):
    theme_changed = Signal(str)
    hotkeys_changed = Signal()

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        s = ctl.settings
        self._checks: dict[str, QCheckBox] = {}
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 8, 0)

        # Models
        g, f = self._group("Models")
        self.folder = QLineEdit(str(ctl.model_dir), readOnly=True)
        browse = QPushButton("Change...")
        browse.clicked.connect(self._choose_folder)
        reset = QPushButton("Default")
        reset.clicked.connect(lambda: self._apply_folder(""))
        row = QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        row.addWidget(reset)
        f.addRow("Model folder", row)
        self.default_model = QComboBox()
        for m in catalog.CATALOG:
            self.default_model.addItem(m.name, m.id)
        self._bind_combo(self.default_model, "default_model")
        f.addRow("Default model", self.default_model)
        f.addRow("", self._check("Download the selected model automatically if it is missing",
                                 "auto_download"))
        col.addWidget(g)

        # Language
        g, f = self._group("Language")
        self.lang = lang = QComboBox()
        for code in ("auto", "en", "es"):
            lang.addItem(LANGUAGE_NAMES[code], code)
        lang.setCurrentIndex(lang.findData(s.language))
        lang.currentIndexChanged.connect(lambda: ctl.set_language(lang.currentData()))
        f.addRow("Spoken language", lang)
        f.addRow("Re-detect every", self._dspin("redetect_seconds", 5, 600, 5, " s"))
        hint = QLabel("Auto mode detects English or Spanish on the first speech and checks again at "
                      "this interval or after a pause. Speech in other languages is not captioned. "
                      "Captions stay in the spoken language (no translation).", wordWrap=True)
        hint.setProperty("muted", True)
        f.addRow(hint)
        col.addWidget(g)

        # Processing
        g, f = self._group("Processing")
        dev = QComboBox()
        for label, val in (("Auto (NVIDIA GPU if it works, else CPU)", "auto"), ("CPU", "cpu"),
                           ("NVIDIA GPU (CUDA)", "cuda")):
            dev.addItem(label, val)
        self._bind_combo(dev, "device")
        f.addRow("Device", dev)
        ct = QComboBox()
        ct.addItem("Auto (float16 on GPU, int8 on CPU)", "auto")
        for t in dict.fromkeys(GPU_COMPUTE_TYPES + CPU_COMPUTE_TYPES):
            ct.addItem(t, t)
        self._bind_combo(ct, "compute_type")
        f.addRow("Compute type", ct)
        beam = QSpinBox(minimum=1, maximum=5, value=s.beam_size)
        beam.valueChanged.connect(lambda v: self._set("beam_size", v))
        beam.setToolTip("1 = lowest latency (default). Higher values are slower.")
        f.addRow("Beam size", beam)
        note = QLabel("Device and compute type apply the next time a model is loaded.", wordWrap=True)
        note.setProperty("muted", True)
        f.addRow(note)
        col.addWidget(g)

        # Audio
        g, f = self._group("Audio")
        self.source = QComboBox()
        self.source.addItem("System audio (what you hear)", "loopback")
        self.source.addItem("Microphone", "microphone")
        self.source.setCurrentIndex(self.source.findData(s.audio_source))
        self.source.currentIndexChanged.connect(self._source_changed)
        f.addRow("Source", self.source)
        self.device = QComboBox()
        self.device.currentIndexChanged.connect(
            lambda: self._set("audio_device", self.device.currentData() or ""))
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.load_devices)
        row = QHBoxLayout()
        row.addWidget(self.device, 1)
        row.addWidget(refresh)
        f.addRow("Device", row)
        pref = QLineEdit(s.preferred_device_name)
        pref.setPlaceholderText("e.g. Poly BT700")
        pref.editingFinished.connect(lambda: self._set("preferred_device_name", pref.text().strip()))
        pref.setToolTip("With the default device selected, a device whose name contains this text is "
                        "used instead when it is connected. Leave empty to always use the default.")
        f.addRow("Prefer device named", pref)
        f.addRow("Chunk length", self._dspin("chunk_seconds", 1.0, 10.0, 0.5, " s"))
        f.addRow("Overlap", self._dspin("overlap_seconds", 0.0, 2.0, 0.1, " s"))
        f.addRow("Max queued audio", self._dspin("max_backlog_seconds", 2.0, 60.0, 1.0, " s"))
        self.audio_msg = QLabel(wordWrap=True)
        self.audio_msg.setProperty("muted", True)
        self.audio_msg.setText("Shorter chunks show captions sooner but give the model less context. "
                               "If processing falls behind by more than the queue limit, the oldest "
                               "audio is skipped so captions stay current. Changes apply on next start.")
        f.addRow(self.audio_msg)
        col.addWidget(g)

        # Captions
        g, f = self._group("Captions")
        hist = QSpinBox(minimum=10, maximum=100000, value=s.history_limit, singleStep=100)
        hist.valueChanged.connect(lambda v: self._set("history_limit", v))
        f.addRow("Lines kept on screen", hist)
        f.addRow("", self._check("Show timestamps", "timestamps"))
        f.addRow("", self._check("Auto-scroll", "autoscroll"))
        f.addRow("", self._check("Type captions into the focused app (off by default)", "auto_type"))
        col.addWidget(g)

        # Shortcuts
        g, f = self._group("Keyboard shortcuts")
        gh = self._check("Global shortcuts (work while other apps are focused)", "global_hotkeys")
        gh.toggled.connect(lambda _: self.hotkeys_changed.emit())
        f.addRow("", gh)
        for label, key in (("Start / stop captions", "hotkey_start_stop"),
                           ("Toggle typing into focused app", "hotkey_auto_type")):
            edit = QLineEdit(getattr(s, key))
            edit.setPlaceholderText("e.g. F9 or Ctrl+Alt+C")
            edit.editingFinished.connect(lambda e=edit, k=key: self._set_hotkey(k, e))
            f.addRow(label, edit)
        col.addWidget(g)

        # Startup and window
        g, f = self._group("Startup and window")
        f.addRow("", self._check("Start captions when the app opens", "auto_start"))
        f.addRow("", self._check("Start minimized", "start_minimized"))
        f.addRow("", self._check("Closing the window keeps the app in the system tray", "close_to_tray"))
        sw = self._check("Start with Windows", "start_with_windows")
        sw.toggled.connect(self._start_with_windows)
        f.addRow("", sw)
        f.addRow("", self._check("Show performance metrics", "show_metrics"))
        theme = QComboBox()
        for label, val in (("Follow Windows", "system"), ("Light", "light"), ("Dark", "dark")):
            theme.addItem(label, val)
        self._bind_combo(theme, "theme")
        theme.currentIndexChanged.connect(lambda: self.theme_changed.emit(theme.currentData()))
        f.addRow("Theme", theme)
        col.addWidget(g)

        # Hardware
        g, f = self._group("Hardware")
        self.hw = {k: QLabel("Detecting...") for k in ("cpu", "ram", "gpu", "vram", "active")}
        for k, label in (("cpu", "CPU"), ("ram", "System RAM"), ("gpu", "GPU"), ("vram", "VRAM"),
                         ("active", "Active inference")):
            self.hw[k].setTextInteractionFlags(Qt.TextSelectableByMouse)
            f.addRow(label, self.hw[k])
        col.addWidget(g)
        col.addStretch()

        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(inner)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.addWidget(QLabel("Settings", objectName="PageTitle"))
        lay.addWidget(scroll, 1)

        ctl.setting_changed.connect(self._sync)
        ctl.hardware_ready.connect(self.show_hardware)
        ctl.engine_state.connect(lambda *_: self.show_active())
        self._devices_loaded = False

    # -- helpers ----------------------------------------------------------------

    def _group(self, title):
        g = QGroupBox(title)
        f = QFormLayout(g)
        f.setLabelAlignment(Qt.AlignLeft)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        return g, f

    def _set(self, name, value):
        self.ctl.set_setting(name, value)

    def _check(self, text, name):
        cb = QCheckBox(text, checked=getattr(self.ctl.settings, name))
        cb.toggled.connect(lambda v: self._set(name, v))
        self._checks[name] = cb
        return cb

    def _sync(self, name, value):
        if name == "language" and self.lang.currentData() != value:
            self.lang.blockSignals(True)
            self.lang.setCurrentIndex(self.lang.findData(value))
            self.lang.blockSignals(False)
        cb = self._checks.get(name)
        if cb is not None and cb.isChecked() != value:
            cb.blockSignals(True)
            cb.setChecked(value)
            cb.blockSignals(False)

    def _dspin(self, name, lo, hi, step, suffix):
        sp = QDoubleSpinBox(minimum=lo, maximum=hi, singleStep=step, decimals=1, suffix=suffix)
        sp.setValue(getattr(self.ctl.settings, name))
        sp.valueChanged.connect(lambda v: self._set_number(name, v))
        return sp

    def _set_number(self, name, value):
        s = self.ctl.settings
        chunk = value if name == "chunk_seconds" else s.chunk_seconds
        overlap = value if name == "overlap_seconds" else s.overlap_seconds
        if overlap >= chunk:
            self.audio_msg.setText("Overlap must be shorter than the chunk length.")
            return
        self._set(name, value)

    def _bind_combo(self, combo, name):
        combo.setCurrentIndex(max(0, combo.findData(getattr(self.ctl.settings, name))))
        combo.currentIndexChanged.connect(lambda: self._set(name, combo.currentData()))

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose model folder", str(self.ctl.model_dir))
        if path:
            self._apply_folder(path)

    def _apply_folder(self, path):
        err = self.ctl.set_model_dir(path)
        if err:
            QMessageBox.warning(self, "Model folder", err)
        self.folder.setText(str(self.ctl.model_dir))

    def _set_hotkey(self, key, edit):
        text = edit.text().strip()
        if winapi.parse_hotkey(text) is None:
            QMessageBox.warning(self, "Keyboard shortcuts",
                                f"'{text}' is not supported. Use F1-F24, a letter or digit, optionally "
                                "with Ctrl, Alt, Shift or Win (e.g. Ctrl+Alt+C).")
            edit.setText(getattr(self.ctl.settings, key))
            return
        self._set(key, text)
        self.hotkeys_changed.emit()

    def _start_with_windows(self, enabled):
        try:
            winapi.set_start_with_windows(enabled)
        except OSError as exc:
            QMessageBox.warning(self, "Start with Windows", f"Could not change the setting:\n{exc}")

    def _source_changed(self):
        self._set("audio_source", self.source.currentData())
        self._set("audio_device", "")
        self.load_devices()

    def load_devices(self):
        """List devices (fast, but only done when the page is shown)."""
        s = self.ctl.settings
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem("Default device", "")
        try:
            devices = audio.list_devices()
        except audio.AudioError as exc:
            devices = []
            self.audio_msg.setText(str(exc))
        want_loop = s.audio_source == "loopback"
        for d in devices:
            if d.loopback == want_loop:
                self.device.addItem(d.label, d.name)
        if s.audio_device and self.device.findData(s.audio_device) < 0:
            self.device.addItem(f"{s.audio_device} (not connected)", s.audio_device)
        self.device.setCurrentIndex(max(0, self.device.findData(s.audio_device)))
        self.device.blockSignals(False)
        self._devices_loaded = True

    def showEvent(self, event):
        if not self._devices_loaded:
            self.load_devices()
        super().showEvent(event)

    def show_hardware(self, hw):
        self.hw["cpu"].setText(f"{hw.cpu_name} ({hw.logical_cores} logical cores)")
        self.hw["ram"].setText(f"{hw.ram_total_gb} GB")
        self.hw["gpu"].setText(f"{hw.gpu_name} (driver {hw.driver})" if hw.gpu_name
                               else "No NVIDIA GPU detected")
        self.hw["vram"].setText(f"{hw.vram_free_mb} MB free of {hw.vram_total_mb} MB"
                                if hw.vram_total_mb else "-")
        self.show_active()

    def show_active(self):
        a = self.ctl.active
        self.hw["active"].setText(f"{a.device.upper()}, {a.compute_type}" if a else "No model loaded")


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        text = QLabel(wordWrap=True, openExternalLinks=True)
        text.setTextFormat(Qt.RichText)
        text.setText(
            f"<h2>{DISPLAY_NAME} {__version__}</h2>"
            "<p>Real-time English and Spanish captions for anything playing on your PC or spoken "
            "into a microphone.</p>"
            "<p><b>Private by design:</b> speech recognition runs entirely on this computer with "
            "Faster-Whisper. Audio and captions are never sent anywhere. The internet is only used "
            "when you download a model.</p>"
            "<p>Models: OpenAI Whisper (MIT license), converted to CTranslate2 by "
            "<a href='https://huggingface.co/Systran'>Systran</a> and "
            "<a href='https://huggingface.co/mobiuslabsgmbh'>Mobius Labs</a>.<br>"
            "Built with Faster-Whisper, CTranslate2, PyAudioWPatch and Qt for Python (PySide6, LGPL).</p>"
            f"<p>Settings: {config.settings_path()}<br>Logs: {config.log_dir()}</p>")
        text.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.addWidget(QLabel("About", objectName="PageTitle"))
        lay.addWidget(text)
        lay.addStretch()
