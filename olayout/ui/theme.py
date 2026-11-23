"""Colours, fonts and the Qt style sheet (a dark, low-glare theme)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory


class C:
    """Named colours used across the UI."""

    WINDOW = QColor("#1b1d21")
    PANEL = QColor("#212329")
    PANEL_ALT = QColor("#272a31")
    INPUT = QColor("#2c2f37")
    BORDER = QColor("#363a43")
    TEXT = QColor("#d9dce1")
    TEXT_DIM = QColor("#8c929c")
    ACCENT = QColor("#4aa8ff")
    ACCENT_SOFT = QColor(74, 168, 255, 60)
    MEASURE = QColor("#ffd166")
    PROBE = QColor("#7ee0c3")
    ERROR = QColor("#ff4d5e")
    ERROR_FILL = QColor(255, 60, 80, 70)
    WARNING = QColor("#ffb347")
    OK = QColor("#3ecf8e")

    CANVAS = QColor("#131418")
    BOARD = QColor("#8d939a")  # stainless / anodized-silver breadboard
    BOARD_EDGE = QColor("#5d636b")
    HOLE = QColor("#202226")
    HOLE_RIM = QColor("#b9bec4")
    GRID_LABEL = QColor("#6c727b")


STYLE_SHEET = f"""
* {{
    color: {C.TEXT.name()};
    font-size: 9pt;
}}
QMainWindow, QDialog {{
    background: {C.WINDOW.name()};
}}
QDockWidget {{
    titlebar-close-icon: none;
}}
QDockWidget::title {{
    background: {C.PANEL.name()};
    padding: 6px 8px;
    border-bottom: 1px solid {C.BORDER.name()};
    font-weight: 600;
    color: {C.TEXT_DIM.name()};
    text-transform: uppercase;
}}
QWidget#Panel, QScrollArea, QScrollArea > QWidget > QWidget {{
    background: {C.PANEL.name()};
}}
QToolBar {{
    background: {C.PANEL.name()};
    border: none;
    border-bottom: 1px solid {C.BORDER.name()};
    spacing: 2px;
    padding: 3px;
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px;
}}
QToolButton:hover {{
    background: {C.PANEL_ALT.name()};
    border-color: {C.BORDER.name()};
}}
QToolButton:checked {{
    background: rgba(74, 168, 255, 0.18);
    border-color: rgba(74, 168, 255, 0.55);
}}
QMenuBar {{
    background: {C.WINDOW.name()};
}}
QMenuBar::item:selected, QMenu::item:selected {{
    background: {C.PANEL_ALT.name()};
}}
QMenu {{
    background: {C.PANEL.name()};
    border: 1px solid {C.BORDER.name()};
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 22px 5px 18px;
    border-radius: 4px;
}}
QMenu::separator {{
    height: 1px;
    background: {C.BORDER.name()};
    margin: 4px 6px;
}}
QStatusBar {{
    background: {C.PANEL.name()};
    border-top: 1px solid {C.BORDER.name()};
    color: {C.TEXT_DIM.name()};
}}
QStatusBar QLabel {{
    color: {C.TEXT_DIM.name()};
    padding: 0 8px;
}}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QPlainTextEdit {{
    background: {C.INPUT.name()};
    border: 1px solid {C.BORDER.name()};
    border-radius: 5px;
    padding: 3px 6px;
    selection-background-color: {C.ACCENT.name()};
}}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {C.ACCENT.name()};
}}
QComboBox QAbstractItemView {{
    background: {C.PANEL.name()};
    border: 1px solid {C.BORDER.name()};
    selection-background-color: {C.PANEL_ALT.name()};
}}
QPushButton {{
    background: {C.INPUT.name()};
    border: 1px solid {C.BORDER.name()};
    border-radius: 5px;
    padding: 5px 12px;
}}
QPushButton:hover {{
    border-color: {C.ACCENT.name()};
}}
QPushButton:pressed {{
    background: {C.PANEL_ALT.name()};
}}
QListWidget, QTreeWidget, QTableWidget {{
    background: {C.PANEL.name()};
    border: none;
    outline: none;
}}
QListWidget::item, QTreeWidget::item {{
    border-radius: 6px;
    padding: 4px;
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background: {C.PANEL_ALT.name()};
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: rgba(74, 168, 255, 0.16);
    color: {C.TEXT.name()};
}}
QHeaderView::section {{
    background: {C.PANEL.name()};
    color: {C.TEXT_DIM.name()};
    border: none;
    border-bottom: 1px solid {C.BORDER.name()};
    padding: 4px;
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: transparent;
    width: 10px;
    height: 10px;
}}
QScrollBar::handle {{
    background: {C.BORDER.name()};
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0;
}}
QLabel#Heading {{
    font-size: 11pt;
    font-weight: 600;
}}
QLabel#Dim {{
    color: {C.TEXT_DIM.name()};
}}
QLabel#Error {{
    color: {C.ERROR.name()};
}}
QLabel#Ok {{
    color: {C.OK.name()};
}}
QFrame#Separator {{
    background: {C.BORDER.name()};
    max-height: 1px;
    min-height: 1px;
}}
QToolTip {{
    background: {C.PANEL_ALT.name()};
    color: {C.TEXT.name()};
    border: 1px solid {C.BORDER.name()};
    padding: 4px;
}}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, C.WINDOW)
    pal.setColor(QPalette.ColorRole.WindowText, C.TEXT)
    pal.setColor(QPalette.ColorRole.Base, C.INPUT)
    pal.setColor(QPalette.ColorRole.AlternateBase, C.PANEL_ALT)
    pal.setColor(QPalette.ColorRole.Text, C.TEXT)
    pal.setColor(QPalette.ColorRole.Button, C.INPUT)
    pal.setColor(QPalette.ColorRole.ButtonText, C.TEXT)
    pal.setColor(QPalette.ColorRole.Highlight, C.ACCENT)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, C.PANEL_ALT)
    pal.setColor(QPalette.ColorRole.ToolTipText, C.TEXT)
    pal.setColor(QPalette.ColorRole.PlaceholderText, C.TEXT_DIM)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, C.TEXT_DIM)
    app.setPalette(pal)
    app.setStyleSheet(STYLE_SHEET)


def wavelength_color(nm: float) -> QColor:
    """Approximate display colour of a wavelength; infrared/UV get vivid stand-ins."""
    if nm >= 760:
        return QColor("#ff3b8d")  # IR: hot pink so it stays visible
    if nm < 380:
        return QColor("#b06bff")  # UV
    if nm < 440:
        r, g, b = -(nm - 440) / 60, 0.0, 1.0
    elif nm < 490:
        r, g, b = 0.0, (nm - 440) / 50, 1.0
    elif nm < 510:
        r, g, b = 0.0, 1.0, -(nm - 510) / 20
    elif nm < 580:
        r, g, b = (nm - 510) / 70, 1.0, 0.0
    elif nm < 645:
        r, g, b = 1.0, -(nm - 645) / 65, 0.0
    else:
        r, g, b = 1.0, 0.0, 0.0
    # Brighten toward white a little so dark violet/red read on the board.
    mix = 0.18
    return QColor.fromRgbF(r + (1 - r) * mix, g + (1 - g) * mix, b + (1 - b) * mix)
