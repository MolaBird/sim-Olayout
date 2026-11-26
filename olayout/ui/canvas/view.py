"""The canvas view: zoom/pan, tool dispatch, drag-and-drop placement."""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QCursor, QInputDevice, QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import QFrame, QGraphicsView

from olayout.ui.canvas.scene import LayoutScene
from olayout.ui.canvas.snapping import SnapSettings, Snapper
from olayout.ui.canvas.tools import (
    AimTool,
    ClampTool,
    CombineTool,
    DrillTool,
    MeasureTool,
    PlaceTool,
    ProbeTool,
    SelectTool,
    Tool,
)
from olayout.ui.document import LayoutDocument

UNIT_MIME = "application/x-olayout-unit"  # carries a library entry key
MIN_PPI, MAX_PPI = 3.0, 4000.0
WHEEL_ZOOM_BASE = 1.0015  # per 1/8° of wheel rotation: ~1.2x per notch
ALT = Qt.KeyboardModifier.AltModifier
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


class LayoutView(QGraphicsView):
    cursorMoved = Signal(float, float)  # model coordinates, inches
    zoomChanged = Signal(float)  # pixels per inch
    toolChanged = Signal(str)
    hintChanged = Signal(str)  # what the active tool expects next
    messageShown = Signal(str)  # a transient outcome, e.g. of the combine tool
    contextMenuRequested = Signal(QPoint, object, object)  # global position, component id or None, (x, y) on the board

    def __init__(self, doc: LayoutDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.layout_scene = LayoutScene(doc, self)
        self.setScene(self.layout_scene)
        self.snap_settings = SnapSettings()
        self.snapper = Snapper(doc, self.snap_settings)

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Zooming is anchored by hand (zoom_at), so Qt must not move the view itself.
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.tools: dict[str, Tool] = {
            "select": SelectTool(self),
            "measure": MeasureTool(self),
            "probe": ProbeTool(self),
            "drill": DrillTool(self),
        }
        self.tool: Tool = self.tools["select"]
        self.tool.activate()
        self._pan_origin: QPoint | None = None
        self._pan_button: Qt.MouseButton | None = None
        self._pan_moved = False
        self._space_held = False
        self._scroll_rest = QPointF(0.0, 0.0)  # sub-pixel part of pending scrolls
        self._drop_tool: PlaceTool | None = None
        doc.layoutReset.connect(self._on_reset)

    # ================================================================ tools
    def set_tool(
        self,
        name: str,
        type_id: str | None = None,
        angle: float = 0.0,
        move_from: str | None = None,
        comp_id: str | None = None,
    ) -> None:
        """Switch tools.  ``type_id``: the library entry to place; ``move_from``:
        the component whose mount to carry; ``comp_id``: the post whose clamp to
        turn, or the component whose beam to aim."""
        self.tool.deactivate()
        if name == "place" and (type_id is not None or move_from is not None):
            self.tool = PlaceTool(self, type_id or "", angle, move_from=move_from)
        elif name == "combine":
            self.tool = CombineTool(self)  # works on the current selection
        elif name == "clamp" and comp_id is not None:
            self.tool = ClampTool(self, comp_id)
        elif name == "aim":
            self.tool = AimTool(self, comp_id)
        else:
            self.tool = self.tools.get(name, self.tools["select"])
        self.tool.activate()
        self.toolChanged.emit(self.tool.name)

    def show_hint(self, text: str) -> None:
        self.hintChanged.emit(text)

    def notify(self, text: str) -> None:
        self.messageShown.emit(text)

    def start_placing(self, type_id: str | None = None, move_from: str | None = None) -> None:
        """Place a library entry (or carry ``move_from``'s mount) where the user clicks."""
        self.set_tool("place", type_id=type_id, move_from=move_from)
        pos = self.mapFromGlobal(self.cursor().pos())
        if self.viewport().rect().contains(pos):
            _, mx, my = self.model_pos(pos)
            self.tool.move_to(mx, my)

    # =============================================================== coords
    def pixels_per_inch(self) -> float:
        t = self.transform()
        return math.hypot(t.m11(), t.m12())

    def model_pos(self, view_pos) -> tuple[QPointF, float, float]:
        scene_pos = self.mapToScene(view_pos)
        return scene_pos, scene_pos.x(), -scene_pos.y()

    # ========================================================== zoom & pan
    def scroll_by(self, dx: float, dy: float) -> None:
        """Move the view by (dx, dy) pixels, keeping fractions for the next call."""
        total = self._scroll_rest + QPointF(dx, dy)
        ix, iy = round(total.x()), round(total.y())
        self._scroll_rest = QPointF(total.x() - ix, total.y() - iy)
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() + ix)
        v.setValue(v.value() + iy)

    def zoom_at(self, factor: float, view_pos: QPointF) -> None:
        """Zoom by ``factor`` keeping the board point under ``view_pos`` fixed."""
        current = self.pixels_per_inch()
        factor = min(max(factor, MIN_PPI / current), MAX_PPI / current)
        if abs(factor - 1.0) < 1e-12:
            return
        anchor = self.viewportTransform().inverted()[0].map(QPointF(view_pos))
        self.scale(factor, factor)
        moved_to = self.viewportTransform().map(anchor)
        self.scroll_by(moved_to.x() - view_pos.x(), moved_to.y() - view_pos.y())
        self.zoomChanged.emit(self.pixels_per_inch())

    def zoom_by(self, factor: float, at_cursor: bool = True) -> None:
        centre = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        pos = centre
        if at_cursor:
            cursor = self.viewport().mapFromGlobal(QCursor.pos())
            if self.viewport().rect().contains(cursor):
                pos = QPointF(cursor)
        self.zoom_at(factor, pos)

    def fit_board(self) -> None:
        rect = self.layout_scene.board_rect().adjusted(-1.2, -1.2, 1.2, 1.2)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoomChanged.emit(self.pixels_per_inch())

    def center_on_model(self, x: float, y: float) -> None:
        self.centerOn(QPointF(x, -y))

    def _on_reset(self) -> None:
        self.resetCachedContent()
        self.viewport().update()

    # ================================================================ input
    def wheelEvent(self, ev: QWheelEvent) -> None:  # noqa: N802
        """Wheel zooms about the cursor (Ctrl+wheel and touchpad pinch too);
        touchpad two-finger scroll and Shift+wheel pan; Alt+wheel rotates."""
        mods = ev.modifiers()
        angle, pixel = ev.angleDelta(), ev.pixelDelta()
        delta = angle.y() or angle.x()  # some platforms swap axes for Alt/Shift
        if mods & ALT:
            if delta:
                step = 0.1 if mods & SHIFT else 1.0
                self.rotate_selection(step * (1 if delta > 0 else -1))
            return
        device = ev.pointingDevice()
        touchpad = device is not None and device.type() == QInputDevice.DeviceType.TouchPad
        if touchpad and not mods & CTRL:
            step = pixel if not pixel.isNull() else angle / 4
            self.scroll_by(-step.x(), -step.y())
            return
        if mods & SHIFT and not mods & CTRL:
            self.scroll_by(-delta, 0.0)
            return
        if delta:
            self.zoom_at(WHEEL_ZOOM_BASE**delta, ev.position())

    def viewportEvent(self, ev) -> bool:  # noqa: N802
        if ev.type() == QEvent.Type.NativeGesture and ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom_at(1.0 + ev.value(), ev.position())  # trackpad pinch (macOS / precision touchpads)
            return True
        return super().viewportEvent(ev)

    def rotate_selection(self, delta: float) -> None:
        if isinstance(self.tool, PlaceTool):
            self.tool.rotate(delta)
            return
        poses = {c.id: (c.x, c.y, round(c.angle + delta, 6)) for c in self.doc.selected_components()}
        if poses:  # small steps in a row (keys, wheel) undo together
            self.doc.transform_components(poses, "Rotate", merge_key="rotate:" + ",".join(sorted(poses)))

    def _begin_pan(self, ev: QMouseEvent) -> None:
        self._pan_origin = ev.position().toPoint()
        self._pan_button = ev.button()
        self._pan_moved = False
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        self.setFocus()
        button = ev.button()
        if button == Qt.MouseButton.MiddleButton or (button == Qt.MouseButton.LeftButton and self._space_held):
            self._begin_pan(ev)
            return
        if button == Qt.MouseButton.RightButton and not isinstance(self.tool, PlaceTool):
            self._begin_pan(ev)
            return
        scene_pos, mx, my = self.model_pos(ev.position().toPoint())
        self.tool.press(ev, scene_pos, mx, my)

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if self._pan_origin is not None:
            pos = ev.position().toPoint()
            delta = pos - self._pan_origin
            if abs(delta.x()) + abs(delta.y()) > 2:
                self._pan_moved = True
            self._pan_origin = pos
            self.scroll_by(-delta.x(), -delta.y())
            return
        scene_pos, mx, my = self.model_pos(ev.position().toPoint())
        self.cursorMoved.emit(mx, my)
        self.tool.move(ev, scene_pos, mx, my)

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if self._pan_origin is not None and ev.button() == self._pan_button:
            was_click = not self._pan_moved and ev.button() == Qt.MouseButton.RightButton
            self._pan_origin = None
            self._pan_button = None
            self.tool.activate()
            if was_click and self.tool.context_click():
                return
            if was_click:
                scene_pos, mx, my = self.model_pos(ev.position().toPoint())
                comp_id = self.layout_scene.component_at(scene_pos)
                if comp_id is not None and comp_id not in self.doc.selection:
                    self.doc.set_selection([comp_id])
                self.contextMenuRequested.emit(ev.globalPosition().toPoint(), comp_id, (mx, my))
            return
        scene_pos, mx, my = self.model_pos(ev.position().toPoint())
        self.tool.release(ev, scene_pos, mx, my)

    def mouseDoubleClickEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        self.mousePressEvent(ev)

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space_held = True
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self.tool.key_press(ev):
            return
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space_held = False
            self.tool.activate()
            return
        super().keyReleaseEvent(ev)

    def leaveEvent(self, ev) -> None:  # noqa: N802
        self.tool.leave()
        super().leaveEvent(ev)

    # ========================================================= drag & drop
    def dragEnterEvent(self, ev) -> None:  # noqa: N802
        if not ev.mimeData().hasFormat(UNIT_MIME):
            ev.ignore()
            return
        key = bytes(ev.mimeData().data(UNIT_MIME)).decode()
        self._drop_tool = PlaceTool(self, key)
        self._drop_tool.activate()
        ev.acceptProposedAction()

    def dragMoveEvent(self, ev) -> None:  # noqa: N802
        if self._drop_tool is None:
            ev.ignore()
            return
        _, mx, my = self.model_pos(ev.position().toPoint())
        self._drop_tool.move_to(mx, my, ev.modifiers())
        ev.acceptProposedAction()

    def dragLeaveEvent(self, ev) -> None:  # noqa: N802
        if self._drop_tool is not None:
            self._drop_tool.deactivate()
            self._drop_tool = None
            self.toolChanged.emit(self.tool.name)  # the status bar shows the current tool again

    def dropEvent(self, ev) -> None:  # noqa: N802
        tool, self._drop_tool = self._drop_tool, None
        if tool is None:
            ev.ignore()
            return
        _, mx, my = self.model_pos(ev.position().toPoint())
        tool.move_to(mx, my, ev.modifiers())
        tool.place()
        tool.deactivate()
        ev.acceptProposedAction()
        self.toolChanged.emit(self.tool.name)
        self.setFocus()
