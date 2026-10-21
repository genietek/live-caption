"""Main window: sidebar navigation, pages, tray icon and shortcuts."""

from __future__ import annotations

from PySide6.QtCore import QAbstractNativeEventFilter, QSize, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QListWidget, QMainWindow, QMenu,
                               QMessageBox, QStackedWidget, QSystemTrayIcon, QWidget)

from .. import DISPLAY_NAME, winapi
from . import theme
from .dashboard import Dashboard
from .models_page import DownloadsPage, ModelsPage
from .settings_page import AboutPage, SettingsPage


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hotkeys: winapi.GlobalHotkeys):
        super().__init__()
        self.hotkeys = hotkeys

    def nativeEventFilter(self, event_type, message):
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            if self.hotkeys.handle_message(message):
                return True, 0
        return False, 0


class MainWindow(QMainWindow):
    PAGES = ("Dashboard", "Models", "Downloads", "Settings", "About")

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self._quitting = False
        self.setWindowTitle(DISPLAY_NAME)
        self.setWindowIcon(theme.app_icon())
        self.resize(980, 680)
        self.setMinimumSize(640, 460)

        self.nav = QListWidget(objectName="Sidebar")
        self.nav.addItems(self.PAGES)
        for i in range(self.nav.count()):
            self.nav.item(i).setSizeHint(QSize(0, 38))
        self.nav.setFixedWidth(170)
        self.nav.setAccessibleName("Navigation")
        self.stack = QStackedWidget()
        self.dashboard = Dashboard(ctl)
        self.models = ModelsPage(ctl)
        self.downloads = DownloadsPage(ctl)
        self.settings = SettingsPage(ctl)
        for page in (self.dashboard, self.models, self.downloads, self.settings, AboutPage()):
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.nav)
        row.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.settings.theme_changed.connect(lambda name: theme.apply(QApplication.instance(), name))
        self.settings.hotkeys_changed.connect(self.register_hotkeys)
        ctl.info.connect(lambda text: QMessageBox.information(self, DISPLAY_NAME, text))
        ctl.gpu_failed.connect(self._offer_cpu)
        ctl.status.connect(lambda msg, err: self.statusBar().showMessage(msg, 8000))
        ctl.download_changed.connect(self._download_notice)

        # In-window shortcuts (always on), global ones are opt-in.
        self._shortcuts: list[QShortcut] = []
        self.hotkeys = winapi.GlobalHotkeys()
        self._filter = HotkeyFilter(self.hotkeys)
        QApplication.instance().installNativeEventFilter(self._filter)
        self.register_hotkeys()
        for i, _ in enumerate(self.PAGES):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda i=i: self.nav.setCurrentRow(i))

        self._make_tray()
        self._proc = None
        self._stats = QTimer(self, interval=2000, timeout=self._process_stats)
        self._stats.start()

    # -- tray -------------------------------------------------------------------

    def _make_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(theme.app_icon(), self)
        self.tray.setToolTip(DISPLAY_NAME)
        menu = QMenu()
        show = QAction("Show", self, triggered=self.show_normal)
        self.tray_toggle = QAction("Start captions", self, triggered=self.ctl.toggle)
        quit_ = QAction("Quit", self, triggered=self.quit)
        menu.addActions([show, self.tray_toggle])
        menu.addSeparator()
        menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_normal() if reason == QSystemTrayIcon.Trigger else None)
        self.ctl.engine_state.connect(
            lambda state, _: self.tray_toggle.setText(
                "Stop captions" if state == "running" else "Start captions"))
        self.tray.show()

    def show_normal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def start_minimized(self):
        if self.tray is not None and self.ctl.settings.close_to_tray:
            self.hide()
        else:
            self.showMinimized()

    # -- shortcuts ----------------------------------------------------------------

    def toggle_auto_type(self):
        value = not self.ctl.settings.auto_type
        self.ctl.set_setting("auto_type", value)
        self.statusBar().showMessage(
            "Typing into the focused app is ON" if value else "Typing into the focused app is OFF", 4000)

    def register_hotkeys(self):
        s = self.ctl.settings
        for sc in self._shortcuts:
            sc.setParent(None)
        self._shortcuts.clear()
        self.hotkeys.unregister_all()
        actions = ((s.hotkey_start_stop, self.ctl.toggle), (s.hotkey_auto_type, self.toggle_auto_type))
        errors = []
        for key, fn in actions:
            if s.global_hotkeys:
                err = self.hotkeys.register(key, fn)
                if err:
                    errors.append(err)
                    continue
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(fn)
            self._shortcuts.append(sc)
        if errors:
            self.statusBar().showMessage(" ".join(errors), 10000)

    # -- notices --------------------------------------------------------------------

    def _offer_cpu(self, model_id, message):
        answer = QMessageBox.question(
            self, "GPU not available",
            f"{message}\n\nLoad the model on the CPU instead? (Settings > Processing changes the default.)")
        if answer == QMessageBox.Yes:
            self.ctl.select_model(model_id, device="cpu")

    def _download_notice(self, st):
        if st.state == "failed":
            self.statusBar().showMessage(f"Download failed: {st.message}", 10000)

    def _process_stats(self):
        if not self.ctl.settings.show_metrics:
            return
        try:
            import psutil

            if self._proc is None:
                self._proc = psutil.Process()
                self._proc.cpu_percent(None)
                return
            cpu = self._proc.cpu_percent(None) / max(1, psutil.cpu_count() or 1)
            rss = self._proc.memory_info().rss / 1024 ** 2
            self.statusBar().showMessage(f"App CPU {cpu:.0f}% · RAM {rss:.0f} MB", 2500)
        except Exception:
            pass

    # -- closing --------------------------------------------------------------------

    def closeEvent(self, event):
        if not self._quitting and self.tray is not None and self.ctl.settings.close_to_tray:
            event.ignore()
            self.hide()
            return
        self._shutdown()
        event.accept()
        QApplication.instance().quit()

    def quit(self):
        self._quitting = True
        self.close()

    def _shutdown(self):
        self.hotkeys.unregister_all()
        if self.tray is not None:
            self.tray.hide()
        self.ctl.shutdown()
