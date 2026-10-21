"""Light and dark themes (Windows 11-like: neutral surfaces, one accent,
rounded cards, Segoe UI) and the app icon, drawn in code so no image files
need to be shipped."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

ACCENT = "#2563eb"

PALETTES = {
    "light": dict(bg="#f3f3f3", surface="#ffffff", sidebar="#ebebeb", text="#1b1b1b",
                  muted="#5f6368", border="#dcdcdc", accent=ACCENT, accent_text="#ffffff",
                  hover="#e3e3e3", ok="#0f7b0f", warn="#9d5d00", err="#c42b1c", input="#ffffff"),
    "dark": dict(bg="#202020", surface="#2b2b2b", sidebar="#1c1c1c", text="#f3f3f3",
                 muted="#a8a8a8", border="#3c3c3c", accent="#60a5fa", accent_text="#0b1220",
                 hover="#363636", ok="#6ccb5f", warn="#fce100", err="#ff99a4", input="#323232"),
}


def system_is_dark(app: QApplication) -> bool:
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return app.palette().color(QPalette.Window).lightness() < 128


def resolve(name: str, app: QApplication) -> str:
    if name in PALETTES:
        return name
    return "dark" if system_is_dark(app) else "light"


def apply(app: QApplication, name: str) -> dict:
    c = PALETTES[resolve(name, app)]
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "input"), (QPalette.AlternateBase, "surface"),
                      (QPalette.Text, "text"), (QPalette.WindowText, "text"), (QPalette.Button, "surface"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"),
                      (QPalette.HighlightedText, "accent_text"), (QPalette.ToolTipBase, "surface"),
                      (QPalette.ToolTipText, "text"), (QPalette.PlaceholderText, "muted")):
        pal.setColor(role, QColor(c[key]))
    app.setPalette(pal)
    app.setStyleSheet(f"""
        QWidget {{ color: {c['text']}; }}
        QMainWindow, QStackedWidget > QWidget {{ background: {c['bg']}; }}
        QListWidget#Sidebar {{ background: {c['sidebar']}; border: none; outline: none; padding: 6px; }}
        QListWidget#Sidebar::item {{ padding-left: 12px; border-radius: 6px; }}
        QListWidget#Sidebar::item:selected {{ background: {c['hover']}; color: {c['text']};
                                   border-left: 3px solid {c['accent']}; }}
        QListWidget#Sidebar::item:hover:!selected {{ background: {c['hover']}; }}
        #PageTitle {{ font-size: 20px; font-weight: 600; }}
        #SectionTitle {{ font-size: 14px; font-weight: 600; margin-top: 8px; }}
        #Muted, QLabel[muted="true"] {{ color: {c['muted']}; }}
        #Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; }}
        #Card[active="true"] {{ border: 2px solid {c['accent']}; }}
        #CardTitle {{ font-size: 14px; font-weight: 600; }}
        QLabel[badge="true"] {{ border-radius: 4px; padding: 1px 6px; font-size: 11px;
                                background: {c['hover']}; }}
        QLabel[badge="accent"] {{ border-radius: 4px; padding: 1px 6px; font-size: 11px;
                                  background: {c['accent']}; color: {c['accent_text']}; }}
        QLabel[tone="ok"] {{ color: {c['ok']}; }}
        QLabel[tone="warn"] {{ color: {c['warn']}; }}
        QLabel[tone="err"] {{ color: {c['err']}; }}
        QPushButton {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 6px;
                       padding: 5px 14px; min-height: 20px; }}
        QPushButton:hover {{ background: {c['hover']}; }}
        QPushButton:focus {{ border: 1px solid {c['accent']}; }}
        QPushButton:disabled {{ color: {c['muted']}; }}
        QPushButton[primary="true"] {{ background: {c['accent']}; color: {c['accent_text']};
                                       border: 1px solid {c['accent']}; font-weight: 600; }}
        QPushButton[primary="true"]:disabled {{ background: {c['hover']}; color: {c['muted']};
                                                border-color: {c['border']}; }}
        QPlainTextEdit, QLineEdit {{
            background: {c['input']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 4px 6px; }}
        QComboBox, QSpinBox, QDoubleSpinBox {{ min-height: 22px; }}
        QPlainTextEdit {{ font-size: 15px; padding: 8px; }}
        QLineEdit:focus, QPlainTextEdit:focus {{
            border: 1px solid {c['accent']}; }}
        QGroupBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px;
                     margin-top: 18px; padding: 12px 10px 8px 10px; font-weight: 600; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
        QGroupBox QLabel, QGroupBox QCheckBox {{ font-weight: normal; }}
        QProgressBar {{ border: none; border-radius: 3px; background: {c['hover']}; max-height: 6px; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QStatusBar {{ background: {c['sidebar']}; }}
    """)
    return c


def app_icon() -> QIcon:
    """Speech bubble with caption lines."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        s = size / 64
        path = QPainterPath()
        path.addRoundedRect(QRectF(4 * s, 6 * s, 56 * s, 40 * s), 10 * s, 10 * s)
        tail = QPainterPath()
        tail.moveTo(16 * s, 44 * s)
        tail.lineTo(14 * s, 58 * s)
        tail.lineTo(30 * s, 44 * s)
        path = path.united(tail)
        p.fillPath(path, QColor(ACCENT))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(QRectF(13 * s, 17 * s, 38 * s, 6 * s), 3 * s, 3 * s)
        p.drawRoundedRect(QRectF(13 * s, 29 * s, 26 * s, 6 * s), 3 * s, 3 * s)
        p.end()
        icon.addPixmap(pm)
    return icon


def write_ico(path: str) -> bool:
    """Save the icon as .ico for the packaged executable."""
    return app_icon().pixmap(256, 256).toImage().save(path, "ICO")


def set_prop(widget, name: str, value) -> None:
    """Change a dynamic property used by the stylesheet and refresh the style."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
