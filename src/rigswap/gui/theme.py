"""A dark theme (Fusion style + palette + a little QSS)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG, PANEL, RAISED, BORDER = "#111318", "#181b22", "#242934", "#323846"
TEXT, MUTED, ACCENT = "#e6e8ee", "#8f97a8", "#4c7dff"

QSS = f"""
QWidget {{ font-size: 13px; }}
QMainWindow, QScrollArea#DetailScroll, QScrollArea#DetailScroll > QWidget > QWidget {{
    background: {BG};
}}
#Header {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QLabel#Title {{ font-size: 20px; font-weight: 600; }}
QLabel#Hint {{ color: {MUTED}; }}
QLabel#Strong {{ font-weight: 600; }}
QPushButton {{
    background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 12px;
}}
QPushButton:hover {{ background: #2d3340; }}
QPushButton:pressed {{ background: #1f232c; }}
QPushButton:disabled {{ color: #5c6273; border-color: #272b35; }}
QPushButton#Primary {{ background: {ACCENT}; border-color: #6a92ff; color: white; }}
QPushButton#Primary:hover {{ background: #5c89ff; }}
QPushButton#SetupButton {{ border-radius: 15px; padding: 6px 18px; }}
QPushButton#SetupButton:checked {{
    background: {ACCENT}; border-color: #6a92ff; color: white; font-weight: 600;
}}
QPushButton#Round {{ border-radius: 15px; padding: 6px 11px; font-weight: 600; }}
QLineEdit {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 8px;
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QListView#Grid {{ background: {BG}; border: none; }}
QListWidget#Locations {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QListWidget#Locations::item {{ padding: 6px; }}
QListWidget#Locations::item:selected {{ background: #2a3550; color: {TEXT}; }}
QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 8px; margin-top: 16px; padding: 12px 10px 10px;
    background: {PANEL};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; }}
QSplitter::handle {{ background: {BORDER}; width: 1px; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, BG), (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, PANEL), (QPalette.ColorRole.AlternateBase, RAISED),
        (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.Button, RAISED),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, RAISED),
        (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.PlaceholderText, MUTED),
    ]:
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#5c6273"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#5c6273"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
