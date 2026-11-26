"""Parts library: thumbnails of every entry; drag onto the board (or onto a
post, for a mount or a KB1X1) or double-click."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QFont, QIcon
from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from olayout.catalog.units import BARE_POST, CATEGORY_ORDER, LIBRARY, LibraryEntry, component_bom, get_unit_type
from olayout.core.layout import Component
from olayout.ui.canvas.painting import unit_thumbnail
from olayout.ui.canvas.view import UNIT_MIME
from olayout.ui.theme import C

TYPE_ROLE = Qt.ItemDataRole.UserRole + 1  # the library entry key
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
        entries = list(LIBRARY.values())
        categories = sorted({e.category for e in entries}, key=lambda c: (CATEGORY_ORDER + (c,)).index(c))
        for category in categories:
            header = QListWidgetItem(category.upper())
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setFont(header_font)
            header.setForeground(C.TEXT_DIM)
            self.list.addItem(header)
            for entry in entries:
                if entry.category == category:
                    self.list.addItem(self._item(entry, dpr))

    @staticmethod
    def _item(entry: LibraryEntry, dpr: float) -> QListWidgetItem:
        unit = get_unit_type(entry.type_id)
        pixmap = unit_thumbnail(entry.type_id, round(THUMB * dpr), params=entry.initial_params())
        pixmap.setDevicePixelRatio(dpr)
        if entry.kb1x1_only:
            subtitle = "KB1X1 · drop on a post"
        elif entry.type_id == BARE_POST:  # what the holder is made of
            bom = component_bom(Component("", BARE_POST, params=entry.initial_params()))
            subtitle = " + ".join(part.replace('TR-series Ø1/2" post', "post") for part in bom)
        elif unit.bill_of_materials:
            subtitle = " + ".join(unit.bill_of_materials[:3])
        else:
            subtitle = "virtual source"
        item = QListWidgetItem(QIcon(pixmap), f"{entry.name}\n{subtitle}")
        item.setData(TYPE_ROLE, entry.key)
        how = ("Drop it onto a bare post, or anywhere to get it on its own post."
               if entry.mounts_on_posts else "Drag onto the board, or double-click to place.")
        item.setToolTip(
            f"<b>{entry.name}</b> <span style='color:#8c929c'>({entry.key})</span><br>{entry.description}"
            + (f"<br><br>Parts: {', '.join(unit.bill_of_materials)}" if unit.bill_of_materials else "")
            + f"<br><br><i>{how}</i>"
        )
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
        return item

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
