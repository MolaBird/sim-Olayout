"""Placement checks: every overlap / unreachable hole, click to jump there."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from olayout.ui.document import LayoutDocument
from olayout.ui.theme import C


def _dot(color: QColor) -> QIcon:
    pix = QPixmap(12, 12)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(color)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, 8, 8)
    p.end()
    return QIcon(pix)


class ChecksPanel(QWidget):
    focusRequested = Signal(object)  # list of component ids

    def __init__(self, doc: LayoutDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.list = QListWidget()
        self.list.itemClicked.connect(self._clicked)
        layout.addWidget(self.list, 1)
        self._error_icon = _dot(C.ERROR)
        self._warn_icon = _dot(C.WARNING)
        doc.analysisUpdated.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        analysis = self.doc.analysis
        self.list.clear()
        for issue in analysis.issues:
            item = QListWidgetItem(self._error_icon, issue.message)
            item.setData(Qt.ItemDataRole.UserRole, list(issue.component_ids))
            self.list.addItem(item)
        for warning in analysis.trace.warnings:
            item = QListWidgetItem(self._warn_icon, warning.message)
            item.setData(Qt.ItemDataRole.UserRole, [warning.owner] if warning.owner else [])
            self.list.addItem(item)
        count = len(analysis.issues)
        if count == 0:
            self.status.setObjectName("Ok")
            self.status.setText("✓ All placements are legal")
        else:
            self.status.setObjectName("Error")
            self.status.setText(f"{count} placement problem(s)")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _clicked(self, item: QListWidgetItem) -> None:
        ids = item.data(Qt.ItemDataRole.UserRole) or []
        if ids:
            self.focusRequested.emit(ids)
