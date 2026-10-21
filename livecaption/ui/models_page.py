"""Models page (catalog cards) and Downloads page."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import catalog
from .theme import set_prop


def fmt_bytes(n: float | None) -> str:
    if n is None:
        return "?"
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    return f"{n / 1e6:.0f} MB"


def fmt_size_mb(mb: int) -> str:
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"


STATE_TONE = {"Ready": "ok", "Installed": "ok", "Failed": "err", "Update available": "warn"}


class ModelCard(QFrame):
    def __init__(self, page, info: catalog.ModelInfo):
        super().__init__(objectName="Card")
        self.page, self.info = page, info
        self.title = QLabel(info.name, objectName="CardTitle")
        self.active_badge = QLabel("Active")
        self.active_badge.setProperty("badge", "accent")
        self.rec_badge = QLabel("Recommended")
        self.rec_badge.setProperty("badge", "true")
        self.state = QLabel()
        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addWidget(self.active_badge)
        head.addWidget(self.rec_badge)
        head.addStretch()
        head.addWidget(self.state)

        desc = QLabel(info.description, wordWrap=True)
        facts = QLabel(
            f"English + Spanish · Speed: {info.speed} · Accuracy: {info.accuracy} · "
            f"Download ≈ {fmt_size_mb(info.approx_download_mb)} · "
            f"Est. RAM ≈ {info.est_ram_gb} GB (CPU int8) / VRAM ≈ {info.est_vram_gb} GB (GPU float16) · "
            "CPU and NVIDIA GPU", wordWrap=True)
        facts.setProperty("muted", True)

        self.detail = QLabel(wordWrap=True)
        self.detail.setProperty("muted", True)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)

        self.btn_download = QPushButton("Download")
        self.btn_download.setProperty("primary", True)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_select = QPushButton("Select")
        self.btn_select.setProperty("primary", True)
        self.btn_delete = QPushButton("Delete")
        self.btn_update = QPushButton("Update")
        btn_details = QPushButton("Details")
        self.btn_download.clicked.connect(lambda: page.ctl.download(info.id))
        self.btn_update.clicked.connect(lambda: page.ctl.download(info.id))
        self.btn_cancel.clicked.connect(lambda: page.ctl.cancel_download(info.id))
        self.btn_select.clicked.connect(lambda: page.ctl.select_model(info.id))
        self.btn_delete.clicked.connect(lambda: page.delete(info))
        btn_details.clicked.connect(lambda: page.details(info))
        for b in (self.btn_download, self.btn_cancel, self.btn_select, self.btn_delete,
                  self.btn_update, btn_details):
            b.setAccessibleName(f"{b.text()} {info.name}")
        buttons = QHBoxLayout()
        buttons.addStretch()
        for b in (self.btn_update, self.btn_delete, btn_details, self.btn_cancel, self.btn_download,
                  self.btn_select):
            buttons.addWidget(b)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.addLayout(head)
        lay.addWidget(desc)
        lay.addWidget(facts)
        lay.addWidget(self.detail)
        lay.addWidget(self.progress)
        lay.addLayout(buttons)

    def update_view(self, ctl, recommended: str | None):
        mid = self.info.id
        state, detail = ctl.model_state(mid)
        installed = mid in ctl.scan.installed
        active = ctl.active is not None and ctl.active.model_id == mid
        self.state.setText(state)
        set_prop(self.state, "tone", STATE_TONE.get(state, ""))
        self.active_badge.setVisible(active)
        self.rec_badge.setVisible(mid == recommended)
        set_prop(self, "active", "true" if active else "false")
        busy_dl = state in ("Downloading", "Verifying")
        st = ctl.downloads.status(mid)
        if busy_dl and st:
            self.progress.setVisible(True)
            if st.total:
                self.progress.setRange(0, 1000)
                self.progress.setValue(int(1000 * st.done / st.total))
            else:
                self.progress.setRange(0, 0)
            speed = f" · {fmt_bytes(st.speed)}/s" if st.speed else ""
            detail = f"{st.message} · {fmt_bytes(st.done)} of {fmt_bytes(st.total)}{speed}"
        else:
            self.progress.setVisible(False)
        if installed and not detail:
            inst = ctl.scan.installed[mid]
            detail = f"{'Imported from' if not inst.managed else 'Stored in'} {inst.path}"
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        loading = ctl.loading_model is not None
        self.btn_download.setVisible(not installed and not busy_dl)
        self.btn_download.setText("Retry" if state == "Failed" and not installed or
                                  (st and st.state == "cancelled" and not installed) else "Download")
        self.btn_cancel.setVisible(busy_dl)
        self.btn_select.setVisible(installed and not active)
        self.btn_select.setEnabled(not loading)
        self.btn_delete.setVisible(installed and not active and ctl.loading_model != mid)
        self.btn_delete.setText("Delete" if installed and ctl.scan.installed[mid].managed else "Remove")
        self.btn_update.setVisible(state == "Update available")


class ModelsPage(QWidget):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        title = QLabel("Models", objectName="PageTitle")
        self.folder_lbl = QLabel(wordWrap=True)
        self.folder_lbl.setProperty("muted", True)
        self.folder_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        import_btn = QPushButton("Import existing models")
        import_btn.setToolTip("Look for already-downloaded models (including C:\\live-caption\\models "
                              "and the Hugging Face cache) and use them without copying")
        import_btn.clicked.connect(ctl.import_existing)
        update_btn = QPushButton("Check for updates")
        update_btn.clicked.connect(ctl.check_updates)
        rescan_btn = QPushButton("Rescan")
        rescan_btn.clicked.connect(ctl.rescan)
        bar = QHBoxLayout()
        bar.addWidget(self.folder_lbl, 1)
        for b in (import_btn, update_btn, rescan_btn):
            bar.addWidget(b)

        self.cards = {m.id: ModelCard(self, m) for m in catalog.CATALOG}
        self.installed_title = QLabel("Installed models", objectName="SectionTitle")
        self.installed_empty = QLabel("No models installed yet. Download one below.")
        self.installed_empty.setProperty("muted", True)
        self.available_title = QLabel("Available models", objectName="SectionTitle")
        self.installed_box = QVBoxLayout()
        self.available_box = QVBoxLayout()
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 6, 0)
        col.addWidget(self.installed_title)
        col.addWidget(self.installed_empty)
        col.addLayout(self.installed_box)
        col.addWidget(self.available_title)
        col.addLayout(self.available_box)
        col.addStretch()
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(inner)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.addWidget(title)
        lay.addLayout(bar)
        lay.addWidget(scroll, 1)
        ctl.models_changed.connect(self.refresh)
        ctl.download_changed.connect(lambda st: self.refresh_card(st.model_id))
        self._placement: dict[str, bool] = {}
        self.refresh()

    def refresh(self):
        ctl = self.ctl
        self.folder_lbl.setText(f"Model folder: {ctl.model_dir}")
        rec = ctl.recommended()
        any_installed = False
        for info in catalog.CATALOG:
            card = self.cards[info.id]
            installed = info.id in ctl.scan.installed
            any_installed |= installed
            if self._placement.get(info.id) != installed:
                (self.installed_box if installed else self.available_box).addWidget(card)
                self._placement[info.id] = installed
            card.update_view(ctl, rec)
        self._reorder()
        self.installed_empty.setVisible(not any_installed)
        self.available_title.setVisible(len(ctl.scan.installed) < len(catalog.CATALOG))

    def _reorder(self):
        # Keep catalog order inside each section.
        for box in (self.installed_box, self.available_box):
            cards = [box.itemAt(i).widget() for i in range(box.count())]
            cards.sort(key=lambda c: [m.id for m in catalog.CATALOG].index(c.info.id))
            for i, c in enumerate(cards):
                box.insertWidget(i, c)

    def refresh_card(self, model_id: str):
        self.cards[model_id].update_view(self.ctl, self.ctl.recommended())

    def delete(self, info: catalog.ModelInfo):
        inst = self.ctl.scan.installed.get(info.id)
        if inst is None:
            return
        if inst.managed:
            q = f"Delete {info.name}? This removes {inst.path} from disk. You can download it again later."
        else:
            q = (f"Stop using {info.name} from {inst.path}? The folder itself is not deleted, "
                 "because it was imported from outside the app's model folder.")
        if QMessageBox.question(self, "Remove model", q) != QMessageBox.Yes:
            return
        err = self.ctl.delete_model(info.id)
        if err:
            QMessageBox.warning(self, "Remove model", err)

    def details(self, info: catalog.ModelInfo):
        ctl = self.ctl
        inst = ctl.scan.installed.get(info.id)
        state, detail = ctl.model_state(info.id)
        lines = [
            f"<b>{info.name}</b> (id: {info.id})",
            f"Repository: huggingface.co/{info.repo}",
            f"Parameters: {info.params_m} million",
            f"Languages: {info.languages}. Only English and Spanish are used by this app; "
            "the weights themselves are the standard multilingual Whisper weights.",
            f"Speed: {info.speed} · Accuracy: {info.accuracy} (relative, from OpenAI's published model table)",
            f"Approximate download: {fmt_size_mb(info.approx_download_mb)}",
            f"Estimated memory: {info.est_ram_gb} GB RAM on CPU (int8), {info.est_vram_gb} GB VRAM on "
            "GPU (float16). Estimates, not measurements.",
            "Runs on: CPU (int8) and NVIDIA CUDA GPUs (float16)",
            f"Status: {state}{' · ' + detail if detail else ''}",
        ]
        if inst:
            lines.append(f"Folder: {inst.path}")
            if inst.revision:
                lines.append(f"Revision: {inst.revision}")
        if ctl.active and ctl.active.model_id == info.id:
            lines.append(f"Loaded on {ctl.active.device.upper()} as {ctl.active.compute_type} "
                         f"in {ctl.active.load_seconds:.1f}s")
        box = QMessageBox(self)
        box.setWindowTitle(info.name)
        box.setTextFormat(Qt.RichText)
        box.setText("<br>".join(lines))
        box.exec()


class DownloadRow(QFrame):
    def __init__(self, ctl, model_id: str):
        super().__init__(objectName="Card")
        self.model_id = model_id
        self.name = QLabel(catalog.get(model_id).name, objectName="CardTitle")
        self.msg = QLabel(wordWrap=True)
        self.msg.setProperty("muted", True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.cancel = QPushButton("Cancel")
        self.retry = QPushButton("Retry")
        self.cancel.clicked.connect(lambda: ctl.cancel_download(model_id))
        self.retry.clicked.connect(lambda: ctl.download(model_id))
        grid = QGridLayout(self)
        grid.addWidget(self.name, 0, 0)
        grid.addWidget(self.cancel, 0, 1)
        grid.addWidget(self.retry, 0, 2)
        grid.addWidget(self.bar, 1, 0, 1, 3)
        grid.addWidget(self.msg, 2, 0, 1, 3)
        grid.setColumnStretch(0, 1)

    def update_view(self, st):
        active = st.state in ("queued", "downloading", "verifying")
        self.cancel.setVisible(active)
        self.retry.setVisible(st.state in ("failed", "cancelled"))
        if st.total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * st.done / st.total))
        else:
            self.bar.setRange(0, 0 if active else 1)
        if st.state == "done":
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
        speed = f" · {fmt_bytes(st.speed)}/s" if st.speed and active else ""
        label = {"queued": "Queued", "downloading": "Downloading", "verifying": "Verifying",
                 "done": "Installed", "failed": "Failed", "cancelled": "Cancelled"}[st.state]
        self.msg.setText(f"{label} · {fmt_bytes(st.done)} of {fmt_bytes(st.total)}{speed}"
                         + (f" · {st.message}" if st.message and st.message != label else ""))
        set_prop(self.msg, "tone", "err" if st.state == "failed" else "")


class DownloadsPage(QWidget):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.rows: dict[str, DownloadRow] = {}
        title = QLabel("Downloads", objectName="PageTitle")
        self.empty = QLabel("No downloads yet. Download models from the Models page.")
        self.empty.setProperty("muted", True)
        note = QLabel("Models download from Hugging Face into your model folder. Cancelled downloads "
                      "keep their partial files and resume where they stopped.", wordWrap=True)
        note.setProperty("muted", True)
        self.box = QVBoxLayout()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.addWidget(title)
        lay.addWidget(note)
        lay.addWidget(self.empty)
        lay.addLayout(self.box)
        lay.addStretch()
        ctl.download_changed.connect(self.on_status)

    def on_status(self, st):
        row = self.rows.get(st.model_id)
        if row is None:
            row = self.rows[st.model_id] = DownloadRow(self.ctl, st.model_id)
            self.box.insertWidget(0, row)
            self.empty.setVisible(False)
        row.update_view(st)
