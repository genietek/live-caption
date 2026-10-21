"""Dashboard: start/stop, status, live captions and caption tools."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from .. import catalog, winapi
from ..engine import LANGUAGE_NAMES
from .theme import set_prop


class Dashboard(QWidget):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        s = ctl.settings
        self._last_language: object = None

        title = QLabel("Dashboard", objectName="PageTitle")

        self.start_btn = QPushButton("Start captions")
        self.start_btn.setProperty("primary", True)
        self.start_btn.setMinimumWidth(150)
        self.start_btn.clicked.connect(ctl.toggle)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.toggled.connect(self._on_pause)

        self.lang_combo = QComboBox()
        for code in ("auto", "en", "es"):
            self.lang_combo.addItem(LANGUAGE_NAMES[code], code)
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(s.language)))
        self.lang_combo.currentIndexChanged.connect(
            lambda: ctl.set_language(self.lang_combo.currentData()))
        self.lang_combo.setAccessibleName("Language")
        self.redetect_btn = QPushButton("Re-detect")
        self.redetect_btn.setToolTip("Detect the spoken language again on the next speech")
        self.redetect_btn.clicked.connect(ctl.redetect_language)

        top = QHBoxLayout()
        top.addWidget(self.start_btn)
        top.addWidget(self.pause_btn)
        top.addStretch()
        top.addWidget(QLabel("Language:"))
        top.addWidget(self.lang_combo)
        top.addWidget(self.redetect_btn)

        # Status grid
        self.model_lbl, self.lang_lbl, self.audio_lbl = QLabel("-"), QLabel("-"), QLabel("-")
        self.proc_lbl, self.state_lbl = QLabel("-"), QLabel("Idle")
        for lbl in (self.audio_lbl, self.state_lbl):
            lbl.setWordWrap(True)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        rows = (("Model", self.model_lbl), ("Language", self.lang_lbl), ("Audio", self.audio_lbl),
                ("Processing", self.proc_lbl), ("Status", self.state_lbl))
        for i, (name, w) in enumerate(rows):
            k = QLabel(name)
            k.setProperty("muted", True)
            grid.addWidget(k, i // 3 * 2, i % 3)
            grid.addWidget(w, i // 3 * 2 + 1, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setVisible(False)

        # Captions
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setPlaceholderText("Captions will appear here.")
        self.text.setMaximumBlockCount(max(10, s.history_limit))
        self.text.setAccessibleName("Live captions")

        self.autoscroll = QCheckBox("Auto-scroll", checked=s.autoscroll)
        self.timestamps = QCheckBox("Timestamps", checked=s.timestamps)
        self.auto_type = QCheckBox("Type into focused app", checked=s.auto_type)
        self.auto_type.setToolTip("Types each caption into whichever app has keyboard focus. "
                                  "Off by default.")
        self.autoscroll.toggled.connect(lambda v: self._set("autoscroll", v))
        self.timestamps.toggled.connect(lambda v: self._set("timestamps", v))
        self.auto_type.toggled.connect(lambda v: self._set("auto_type", v))
        clear_btn, copy_btn, save_btn = QPushButton("Clear"), QPushButton("Copy"), QPushButton("Save TXT")
        clear_btn.clicked.connect(self.text.clear)
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        save_btn.clicked.connect(self._save_txt)
        tools = QHBoxLayout()
        for w in (self.autoscroll, self.timestamps, self.auto_type):
            tools.addWidget(w)
        tools.addStretch()
        for w in (clear_btn, copy_btn, save_btn):
            tools.addWidget(w)

        self.metrics_lbl = QLabel("")
        self.metrics_lbl.setProperty("muted", True)
        self.metrics_lbl.setVisible(s.show_metrics)
        self.message_lbl = QLabel("")
        self.message_lbl.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.addWidget(title)
        layout.addLayout(top)
        layout.addLayout(grid)
        layout.addWidget(self.busy)
        layout.addWidget(self.message_lbl)
        layout.addWidget(self.text, 1)
        layout.addLayout(tools)
        layout.addWidget(self.metrics_lbl)

        ctl.engine_state.connect(self.on_state)
        ctl.caption.connect(self.on_caption)
        ctl.status.connect(self.on_status)
        ctl.metrics.connect(self.on_metrics)
        ctl.language_changed.connect(self.on_language)
        ctl.models_changed.connect(self.refresh)
        ctl.setting_changed.connect(self._sync)
        self.refresh()
        self.on_language(None if s.language == "auto" else s.language)

    def _set(self, name, value):
        self.ctl.set_setting(name, value)

    def _sync(self, name, value):
        if name == "language" and self.lang_combo.currentData() != value:
            self.lang_combo.blockSignals(True)
            self.lang_combo.setCurrentIndex(self.lang_combo.findData(value))
            self.lang_combo.blockSignals(False)
        box = {"autoscroll": self.autoscroll, "timestamps": self.timestamps,
               "auto_type": self.auto_type}.get(name)
        if box is not None and box.isChecked() != value:
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        if name == "history_limit":
            self.text.setMaximumBlockCount(max(10, value))
        if name == "show_metrics":
            self.metrics_lbl.setVisible(value)
        if name in ("device", "default_model", "audio_source", "audio_device"):
            self.refresh()

    def _on_pause(self, paused: bool):
        self.pause_btn.setText("Resume" if paused else "Pause")
        self.ctl.set_paused(paused)

    def refresh(self):
        ctl = self.ctl
        if ctl.active:
            name = catalog.get(ctl.active.model_id).name
            self.proc_lbl.setText(f"{ctl.active.device.upper()} ({ctl.active.compute_type})")
        else:
            mid = ctl.loading_model or ctl.settings.default_model
            name = catalog.get(mid).name if mid in catalog.BY_ID else "-"
            if mid not in ctl.scan.installed:
                name += " (not downloaded)"
            self.proc_lbl.setText({"auto": "Auto (GPU if available)", "cpu": "CPU",
                                   "cuda": "GPU"}[ctl.settings.device])
        self.model_lbl.setText(name)
        s = ctl.settings
        src = "System audio" if s.audio_source == "loopback" else "Microphone"
        if ctl.transcriber is None:
            self.audio_lbl.setText(f"{src}: {s.audio_device or 'default device'}")

    def on_state(self, state: str, message: str):
        running = state == "running" or self.ctl._start_after_load
        self.start_btn.setText("Stop captions" if running else "Start captions")
        self.start_btn.setEnabled(state != "loading" or self.ctl._start_after_load)
        self.pause_btn.setEnabled(state == "running")
        if state != "running":
            self.pause_btn.blockSignals(True)
            self.pause_btn.setChecked(False)
            self.pause_btn.setText("Pause")
            self.pause_btn.blockSignals(False)
        self.busy.setVisible(state == "loading")
        self.state_lbl.setText(message or state.capitalize())
        set_prop(self.state_lbl, "tone", "err" if state == "error" else "")
        self.refresh()

    def on_status(self, message: str, is_error: bool):
        if message.startswith("Capturing "):
            self.audio_lbl.setText(message[len("Capturing "):])
            self.message_lbl.setText("")
            return
        self.message_lbl.setText(message)
        set_prop(self.message_lbl, "tone", "err" if is_error else "")

    def on_language(self, lang):
        if lang == "other":
            text = "Other language detected (not captioned)"
        elif lang in ("en", "es"):
            text = LANGUAGE_NAMES[lang]
            if self.ctl.settings.language == "auto":
                text += " (detected)"
        else:
            text = "Auto: detecting..." if self.ctl.settings.language == "auto" else "-"
        self.lang_lbl.setText(text)

    def on_caption(self, cap: dict):
        line = cap["text"]
        if self.ctl.settings.timestamps:
            line = time.strftime("[%H:%M:%S] ", time.localtime(cap["time"])) + line
        bar = self.text.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        cursor = QTextCursor(self.text.document())
        cursor.movePosition(QTextCursor.End)
        if not self.text.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(line)
        if self.ctl.settings.autoscroll and at_end:
            bar.setValue(bar.maximum())
        if self.ctl.settings.auto_type:
            self.type_caption(cap["text"])

    def type_caption(self, text: str):
        # Never type into our own window; it would only land in our widgets.
        if QGuiApplication.applicationState() == Qt.ApplicationActive:
            return
        winapi.type_text(text + " ")

    def on_metrics(self, m):
        if not self.metrics_lbl.isVisible():
            return
        self.metrics_lbl.setText(
            f"Inference {m.last_inference_ms:.0f} ms (avg {m.avg_inference_ms:.0f}) · "
            f"real-time factor {m.realtime_factor:.2f} · chunk end to caption {m.last_delay_ms:.0f} ms · "
            f"queued {m.backlog_seconds:.1f}s · dropped {m.dropped_seconds:.1f}s · "
            f"silent {m.skipped_silent} · filtered {m.skipped_suspicious}")

    def _save_txt(self):
        default = str(Path.home() / time.strftime("captions-%Y%m%d-%H%M%S.txt"))
        path, _ = QFileDialog.getSaveFileName(self, "Save captions", default, "Text files (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.text.toPlainText() + "\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Save captions", f"Could not save the file:\n{exc}")
