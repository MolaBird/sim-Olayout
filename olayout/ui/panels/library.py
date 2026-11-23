"""Parts library: thumbnails of every unit; drag onto the board or double-click."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QFont, QIcon
from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from olayout.catalog.units import CATEGORY_ORDER, UNIT_TYPES
from olayout.ui.canvas.painting import unit_thumbnail
from olayout.ui.canvas.view import UNIT_MIME
from olayout.ui.theme import C

TYPE_ROLE = Qt.ItemDataRole.UserRole + 1
THUMB = 52


class _UnitList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setIconSize(QSize(THUMB, THUMB))
        self.setSpacing(1)
        self.setUniformItemSizes(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def startDrag(self, actions) -> None:  # noqa: N802
        item = self.currentItem()
        type_id = item.data(TYPE_ROLE) if item else None
        if not type_id:
            return
        mime = QMimeData()
        mime.setData(UNIT_MIME, type_id.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(THUMB, THUMB))
        drag.exec(Qt.DropAction.CopyAction)


class LibraryPanel(QWidget):
    placeRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter parts…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.list = _UnitList()
        self.list.itemDoubleClicked.connect(self._activate)
        self.list.itemActivated.connect(self._activate)
        layout.addWidget(self.list, 1)
        self._populate()

    def _populate(self) -> None:
        dpr = self.devicePixelRatioF()
        header_font = QFont()
        header_font.setBold(True)
        header_font.setPointSizeF(8.0)
        categories = sorted({u.category for u in UNIT_TYPES.values()}, key=lambda c: (CATEGORY_ORDER + (c,)).index(c))
        for category in categories:
            header = QListWidgetItem(category.upper())
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setFont(header_font)
            header.setForeground(C.TEXT_DIM)
            self.list.addItem(header)
            for unit in UNIT_TYPES.values():
                if unit.category != category:
                    continue
                pixmap = unit_thumbnail(unit.id, round(THUMB * dpr))
                pixmap.setDevicePixelRatio(dpr)
                bom = " + ".join(unit.bill_of_materials[:3]) if unit.bill_of_materials else "virtual source"
                item = QListWidgetItem(QIcon(pixmap), f"{unit.name}\n{bom}")
                item.setData(TYPE_ROLE, unit.id)
                item.setToolTip(
                    f"<b>{unit.name}</b> <span style='color:#8c929c'>({unit.id})</span><br>{unit.description}"
                    + (f"<br><br>Parts: {', '.join(unit.bill_of_materials)}" if unit.bill_of_materials else "")
                    + "<br><br><i>Drag onto the board, or double-click to place.</i>"
                )
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
                self.list.addItem(item)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            type_id = item.data(TYPE_ROLE)
            if type_id is None:
                item.setHidden(bool(needle))
                continue
            haystack = (item.text() + " " + type_id + " " + item.toolTip()).lower()
            item.setHidden(bool(needle) and needle not in haystack)

    def _activate(self, item: QListWidgetItem) -> None:
        type_id = item.data(TYPE_ROLE)
        if type_id:
            self.placeRequested.emit(type_id)
