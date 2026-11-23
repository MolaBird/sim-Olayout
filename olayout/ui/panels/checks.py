"""Placement checks: every overlap, unreachable hole and beam warning.

Click an entry to jump to its components.  Any entry can be *ignored* when
the user knows better (e.g. parts that only overlap in the simplified
model): it stays listed, dimmed, but no longer counts as a problem or
highlights the parts.  Ignoring is saved with the layout and undoable.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from olayout.ui.document import LayoutDocument
from olayout.ui.theme import C

IDS_ROLE = Qt.ItemDataRole.UserRole  # component ids to focus
KEY_ROLE = Qt.ItemDataRole.UserRole + 1  # IssueKey of the entry
IGNORED_ROLE = Qt.ItemDataRole.UserRole + 2  # bool


def _dot(color: QColor, hollow: bool = False) -> QIcon:
    pix = QPixmap(12, 12)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if hollow:
        p.setPen(QPen(color, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
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
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemClicked.connect(self._clicked)
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        self.ignore_button = QPushButton("Ignore")
        self.ignore_button.setToolTip(
            "Accept the selected problems: they stay listed, dimmed, but no longer count\n"
            "as placement problems or highlight the parts. Saved with the layout (undo: Ctrl+Z)."
        )
        self.ignore_button.clicked.connect(lambda: self._set_selected_ignored(True))
        self.restore_button = QPushButton("Restore")
        self.restore_button.setToolTip("Count the selected ignored problems again")
        self.restore_button.clicked.connect(lambda: self._set_selected_ignored(False))
        self.restore_all_button = QPushButton("Restore all")
        self.restore_all_button.setToolTip("Stop ignoring every problem in this layout")
        self.restore_all_button.clicked.connect(self.doc.restore_all_ignored)
        for button in (self.ignore_button, self.restore_button, self.restore_all_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._icons = {
            "error": _dot(C.ERROR),
            "warning": _dot(C.WARNING),
            "ignored": _dot(C.TEXT_DIM, hollow=True),
        }
        doc.analysisUpdated.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------- content
    def refresh(self) -> None:
        analysis = self.doc.analysis
        selected = {item.data(KEY_ROLE) for item in self.list.selectedItems()}
        self.list.clear()
        entries = [(i, "error", False) for i in analysis.active_issues]
        entries += [(w, "warning", False) for w in analysis.active_warnings]
        entries += [(i, "ignored", True) for i in analysis.ignored_issues]
        entries += [(w, "ignored", True) for w in analysis.ignored_warnings]
        for entry, icon, ignored in entries:
            item = QListWidgetItem(self._icons[icon], entry.message + ("  (ignored)" if ignored else ""))
            item.setData(IDS_ROLE, list(entry.key.components))
            item.setData(KEY_ROLE, entry.key)
            item.setData(IGNORED_ROLE, ignored)
            if ignored:
                item.setForeground(QBrush(C.TEXT_DIM))
            self.list.addItem(item)
            item.setSelected(entry.key in selected)

        count, n_ignored = len(analysis.active_issues), len(self.doc.layout.ignored)
        suffix = f" · {n_ignored} ignored" if n_ignored else ""
        if count == 0:
            self.status.setObjectName("Ok")
            self.status.setText("✓ All placements are legal" + suffix)
        else:
            self.status.setObjectName("Error")
            self.status.setText(f"{count} placement problem(s)" + suffix)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self._update_buttons()

    def _selected(self, ignored: bool) -> list:
        return [item.data(KEY_ROLE) for item in self.list.selectedItems() if item.data(IGNORED_ROLE) == ignored]

    def _update_buttons(self) -> None:
        self.ignore_button.setEnabled(bool(self._selected(ignored=False)))
        self.restore_button.setEnabled(bool(self._selected(ignored=True)))
        self.restore_all_button.setVisible(bool(self.doc.layout.ignored))

    # ------------------------------------------------------------- actions
    def _set_selected_ignored(self, ignored: bool) -> None:
        keys = self._selected(ignored=not ignored)
        if keys:
            self.doc.set_ignored(keys, ignored)

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        if self._selected(ignored=False):
            menu.addAction("Ignore", lambda: self._set_selected_ignored(True))
        if self._selected(ignored=True):
            menu.addAction("Restore", lambda: self._set_selected_ignored(False))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _clicked(self, item: QListWidgetItem) -> None:
        ids = item.data(IDS_ROLE) or []
        if ids:
            self.focusRequested.emit(ids)
