"""Interaction tools for the canvas (select/move/rotate, measure, probe, place).

The view forwards mouse/key events to the active tool with both the scene
position and the model position (inches, y up).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QGraphicsRectItem

from olayout.catalog.parts import Layer
from olayout.catalog.units import Mounting, default_fork, get_unit_type, new_component_params
from olayout.core.layout import Component
from olayout.engine.analysis import Analyzer
from olayout.engine.fork import resolve_fork
from olayout.optics.tracer import TraceResult
from olayout.ui.canvas.items import ComponentLayerItem, MeasurementItem, cosmetic_pen
from olayout.ui.canvas.snapping import SNAP_PX, aim_angle, constrain_angle
from olayout.ui.theme import C

if TYPE_CHECKING:
    from olayout.ui.canvas.view import LayoutView

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
DRAG_THRESHOLD_PX = 4.0


class Tool:
    name = "tool"
    cursor = Qt.CursorShape.ArrowCursor

    def __init__(self, view: LayoutView):
        self.view = view

    @property
    def doc(self):
        return self.view.doc

    @property
    def scene(self):
        return self.view.layout_scene

    @property
    def scale(self) -> float:
        return self.view.pixels_per_inch()

    def activate(self) -> None:
        self.view.viewport().setCursor(self.cursor)

    def deactivate(self) -> None:
        self.scene.snap_indicator.setVisible(False)
        self.scene.hover_label.setVisible(False)

    def press(self, ev: QMouseEvent, scene_pos: QPointF, mx: float, my: float) -> None: ...

    def move(self, ev: QMouseEvent, scene_pos: QPointF, mx: float, my: float) -> None: ...

    def release(self, ev: QMouseEvent, scene_pos: QPointF, mx: float, my: float) -> None: ...

    def key_press(self, ev: QKeyEvent) -> bool:
        return False

    def leave(self) -> None:
        self.scene.hover_label.setVisible(False)
        self.scene.set_hovered(None)

    # Shared: beam readout near the cursor.
    def show_beam_readout(self, mx: float, my: float) -> bool:
        readout = self.scene.beam_readout(mx, my, 7.0 / self.scale)
        label = self.scene.hover_label
        if readout is None:
            label.setVisible(False)
            return False
        text, anchor = readout
        label.set_text(text)
        label.setPos(anchor)
        label.setVisible(True)
        return True


# ==========================================================================
# Select / move / rotate
# ==========================================================================
class SelectTool(Tool):
    name = "select"

    def __init__(self, view: LayoutView):
        super().__init__(view)
        self.mode: str | None = None  # press | drag | rotate | band
        self.press_scene = QPointF()
        self.press_model = (0.0, 0.0)
        self.anchor_id: str | None = None
        self.start_poses: dict[str, tuple[float, float, float]] = {}
        self.beam_targets: TraceResult | None = None
        self.lock_to_holes = False
        self.aim: tuple[tuple[float, float], tuple[float, float]] | None = None
        self.band: QGraphicsRectItem | None = None
        self.band_base: list[str] = []

    # ------------------------------------------------------------ helpers
    def _on_handle(self, scene_pos: QPointF) -> bool:
        overlay = self.scene.selection_overlay
        if not overlay.active:
            return False
        handle = overlay.handle_pos(self.scale)
        return math.hypot(handle.x() - scene_pos.x(), handle.y() - scene_pos.y()) * self.scale <= 9.0

    def _incoming_beam(self, comp_id: str):
        for seg in self.doc.analysis.trace.segments:
            if seg.end_owner == comp_id and seg.end_event in ("reflect", "split"):
                return seg.direction, seg.end
        return None

    # -------------------------------------------------------------- events
    def press(self, ev, scene_pos, mx, my):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        mods = ev.modifiers()
        if self._on_handle(scene_pos):
            comp = self.doc.selected_components()[0]
            self.mode = "rotate"
            self.start_poses = {comp.id: (comp.x, comp.y, comp.angle)}
            self.aim = self._incoming_beam(comp.id)
            self.doc.begin_interaction()
            return

        comp_id = self.scene.component_at(scene_pos)
        if comp_id is not None:
            self.scene.set_annotation_selection(set())
            selection = list(self.doc.selection)
            if mods & (CTRL | SHIFT):
                if comp_id in selection:
                    selection.remove(comp_id)
                    self.doc.set_selection(selection)
                    self.mode = None
                    return
                self.doc.set_selection(selection + [comp_id])
            elif comp_id not in selection:
                self.doc.set_selection([comp_id])
            self.mode = "press"
            self.anchor_id = comp_id
            self.press_scene = scene_pos
            self.press_model = (mx, my)
            return

        ann = self.scene.annotation_at(scene_pos, 6.0 / self.scale)
        if ann is not None:
            self.doc.set_selection([])
            self.scene.set_annotation_selection({ann})
            self.mode = None
            return

        self.scene.set_annotation_selection(set())
        if not mods & (CTRL | SHIFT):
            self.doc.set_selection([])
        self.band_base = list(self.doc.selection)
        self.mode = "band"
        self.press_scene = scene_pos
        self.band = QGraphicsRectItem()
        self.band.setPen(cosmetic_pen(C.ACCENT, 1.0, Qt.PenStyle.DashLine))
        self.band.setBrush(QColor(74, 168, 255, 30))
        self.band.setZValue(100)
        self.scene.addItem(self.band)

    def move(self, ev, scene_pos, mx, my):
        if self.mode is None:
            self._hover(scene_pos, mx, my)
            return
        if self.mode == "press":
            delta = scene_pos - self.press_scene
            if math.hypot(delta.x(), delta.y()) * self.scale < DRAG_THRESHOLD_PX:
                return
            self._start_drag()
        if self.mode == "drag":
            self._drag_to(mx, my, ev.modifiers())
        elif self.mode == "rotate":
            self._rotate_to(mx, my, ev.modifiers())
        elif self.mode == "band" and self.band is not None:
            rect = QRectF(self.press_scene, scene_pos).normalized()
            self.band.setRect(rect)
            self.doc.set_selection(self.band_base + self.scene.components_in(rect))

    def release(self, ev, scene_pos, mx, my):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        mode, self.mode = self.mode, None
        if mode == "press":
            if not ev.modifiers() & (CTRL | SHIFT) and len(self.doc.selection) > 1:
                self.doc.set_selection([self.anchor_id])
        elif mode == "drag":
            self.doc.end_interaction("Move")
            self.scene.snap_indicator.setVisible(False)
        elif mode == "rotate":
            self.doc.end_interaction("Rotate")
        elif mode == "band" and self.band is not None:
            self.scene.removeItem(self.band)
            self.band = None

    def key_press(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            if self.mode in ("drag", "rotate"):
                self.doc.cancel_interaction()
                self.scene.snap_indicator.setVisible(False)
            elif self.mode == "band" and self.band is not None:
                self.scene.removeItem(self.band)
                self.band = None
            else:
                self.doc.set_selection([])
                self.scene.set_annotation_selection(set())
            self.mode = None
            return True
        return False

    # ---------------------------------------------------------------- drag
    def _start_drag(self) -> None:
        self.mode = "drag"
        self.doc.begin_interaction()
        comps = self.doc.selected_components()
        self.start_poses = {c.id: (c.x, c.y, c.angle) for c in comps}
        others = [u for cid, u in self.doc.analysis.units.items() if cid not in self.start_poses]
        self.beam_targets = Analyzer.trace(others, self.doc.layout)
        self.lock_to_holes = any(get_unit_type(c.type).mounting is Mounting.HOLE for c in comps)
        self.scene.hover_label.setVisible(False)

    def _drag_to(self, mx: float, my: float, mods) -> None:
        anchor = self.doc.layout.component(self.anchor_id)
        if anchor is None or self.anchor_id not in self.start_poses:
            return
        ax0, ay0, _ = self.start_poses[self.anchor_id]
        raw_x = ax0 + mx - self.press_model[0]
        raw_y = ay0 + my - self.press_model[1]
        snap = self.view.snapper.component(anchor, raw_x, raw_y, self.scale, self.beam_targets, bool(mods & ALT))
        dx, dy = snap.x - ax0, snap.y - ay0
        anchor_on_holes = get_unit_type(anchor.type).mounting is Mounting.HOLE
        if self.lock_to_holes and not anchor_on_holes:
            pitch = self.doc.layout.breadboard.pitch
            dx, dy = round(dx / pitch) * pitch, round(dy / pitch) * pitch
        for cid, (x0, y0, _a0) in self.start_poses.items():
            comp = self.doc.layout.component(cid)
            if comp is not None:
                comp.x, comp.y = x0 + dx, y0 + dy
        self.doc.interaction_changed(set(self.start_poses))
        if snap.marker is not None and snap.kind in ("hole", "beam"):
            color = C.ACCENT if snap.kind == "hole" else C.PROBE
            self.scene.snap_indicator.show_at(*snap.marker, color=color)
        else:
            self.scene.snap_indicator.setVisible(False)

    def _rotate_to(self, mx: float, my: float, mods) -> None:
        (cid, (x0, y0, _a0)), = self.start_poses.items()
        comp = self.doc.layout.component(cid)
        if comp is None:
            return
        angle = None
        if mods & ALT and self.aim is not None:
            angle = aim_angle(self.aim[0], self.aim[1], (mx, my))
        if angle is None:
            raw = math.degrees(math.atan2(my - y0, mx - x0))
            angle = self.view.snapper.rotation(raw, free=bool(mods & SHIFT), fine=bool(mods & CTRL))
        comp.angle = angle % 360.0
        self.doc.interaction_changed({cid})

    def _hover(self, scene_pos: QPointF, mx: float, my: float) -> None:
        on_handle = self._on_handle(scene_pos)
        overlay = self.scene.selection_overlay
        if overlay.hot != on_handle:
            overlay.hot = on_handle
            overlay.update()
        comp_id = None if on_handle else self.scene.component_at(scene_pos)
        self.scene.set_hovered(comp_id)
        if on_handle:
            cursor = Qt.CursorShape.CrossCursor
        elif comp_id is not None:
            cursor = Qt.CursorShape.SizeAllCursor if comp_id in self.doc.selection else Qt.CursorShape.PointingHandCursor
        else:
            cursor = Qt.CursorShape.ArrowCursor
        self.view.viewport().setCursor(cursor)
        if comp_id is None and not on_handle:
            self.show_beam_readout(mx, my)
        else:
            self.scene.hover_label.setVisible(False)


# ==========================================================================
# Measure
# ==========================================================================
class MeasureTool(Tool):
    name = "measure"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, view: LayoutView):
        super().__init__(view)
        self.start: tuple[float, float] | None = None
        self.preview: MeasurementItem | None = None

    def _snap(self, mx, my, mods):
        return self.view.snapper.point(mx, my, self.scale, disabled=bool(mods & ALT))

    def press(self, ev, scene_pos, mx, my):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        snap = self._snap(mx, my, ev.modifiers())
        self.start = (snap.x, snap.y)
        self.preview = MeasurementItem(preview=True)
        self.preview.set_points(self.start, self.start)
        self.scene.addItem(self.preview)

    def move(self, ev, scene_pos, mx, my):
        snap = self._snap(mx, my, ev.modifiers())
        end = (snap.x, snap.y)
        if self.start is not None and ev.modifiers() & SHIFT:
            end = constrain_angle(*self.start, *end)
        if snap.marker is not None:
            self.scene.snap_indicator.show_at(*snap.marker, color=C.MEASURE)
        else:
            self.scene.snap_indicator.setVisible(False)
        if self.start is not None and self.preview is not None:
            self.preview.set_points(self.start, end)

    def release(self, ev, scene_pos, mx, my):
        if self.start is None or self.preview is None:
            return
        a, b = self.preview.endpoints_model()
        self.scene.removeItem(self.preview)
        self.preview = None
        self.start = None
        if math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-3:
            self.doc.add_measurement(a, b)

    def key_press(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            if self.preview is not None:
                self.scene.removeItem(self.preview)
                self.preview = None
                self.start = None
            else:
                self.view.set_tool("select")
            return True
        return False

    def deactivate(self):
        super().deactivate()
        if self.preview is not None:
            self.scene.removeItem(self.preview)
            self.preview = None
        self.start = None


# ==========================================================================
# Probe
# ==========================================================================
class ProbeTool(Tool):
    name = "probe"
    cursor = Qt.CursorShape.CrossCursor

    def press(self, ev, scene_pos, mx, my):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        hit = self.doc.analysis.trace.nearest((mx, my), SNAP_PX / self.scale)
        if hit is not None:
            seg, s, _ = hit
            self.doc.add_probe(*seg.point_at(s))

    def move(self, ev, scene_pos, mx, my):
        hit = self.doc.analysis.trace.nearest((mx, my), SNAP_PX / self.scale)
        if hit is not None:
            seg, s, _ = hit
            self.scene.snap_indicator.show_at(*seg.point_at(s), color=C.PROBE)
        else:
            self.scene.snap_indicator.setVisible(False)
        self.show_beam_readout(mx, my)

    def key_press(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.view.set_tool("select")
            return True
        return False


# ==========================================================================
# Place (ghost follows the cursor)
# ==========================================================================
class PlaceTool(Tool):
    name = "place"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, view: LayoutView, type_id: str, angle: float = 0.0):
        super().__init__(view)
        self.type_id = type_id
        self.comp = Component(id="__ghost__", type=type_id, angle=angle, params=new_component_params(type_id))
        self.items: dict[Layer, ComponentLayerItem] = {}
        self.has_position = False

    def activate(self):
        super().activate()
        for layer in Layer:
            item = ComponentLayerItem(self.comp.id, layer, ghost=True)
            item.setVisible(False)
            self.scene.addItem(item)
            self.items[layer] = item
        self._refresh_geometry()

    def deactivate(self):
        super().deactivate()
        for item in self.items.values():
            self.scene.removeItem(item)
        self.items.clear()

    def _refresh_geometry(self) -> None:
        unit = get_unit_type(self.type_id)
        fork = None
        if unit.mounting is Mounting.PEDESTAL:
            fork = resolve_fork(self.comp, self.doc.layout.breadboard, []) if self.has_position else default_fork()
        placed = self.doc.analyzer.cache.place(self.comp, fork)
        for item in self.items.values():
            item.set_geometry(placed)
            item.set_pose(self.comp.x, self.comp.y, self.comp.angle)

    def move_to(self, mx: float, my: float, mods=Qt.KeyboardModifier.NoModifier) -> None:
        snap = self.view.snapper.component(self.comp, mx, my, self.scale, self.doc.analysis.trace, bool(mods & ALT))
        self.comp.x, self.comp.y = snap.x, snap.y
        self.has_position = True
        self._refresh_geometry()
        for item in self.items.values():
            item.setVisible(item.has_content())
        if snap.marker is not None and snap.kind in ("hole", "beam"):
            self.scene.snap_indicator.show_at(*snap.marker, color=C.ACCENT if snap.kind == "hole" else C.PROBE)
        else:
            self.scene.snap_indicator.setVisible(False)

    def move(self, ev, scene_pos, mx, my):
        self.move_to(mx, my, ev.modifiers())

    def press(self, ev, scene_pos, mx, my):
        if ev.button() == Qt.MouseButton.RightButton:
            self.view.set_tool("select")
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        self.move_to(mx, my, ev.modifiers())
        self.place()
        if not ev.modifiers() & SHIFT:
            self.view.set_tool("select")

    def place(self) -> str:
        return self.doc.add_component(
            self.type_id,
            self.comp.x,
            self.comp.y,
            self.comp.angle,
            params=dict(self.comp.params),
            orientation=self.comp.orientation,
        )

    # Rotation / orientation keys arrive through the main window's actions.
    def rotate(self, delta: float) -> None:
        self.comp.angle = (self.comp.angle + delta) % 360.0
        self._refresh_geometry()

    def cycle_orientation(self) -> None:
        options = [name for name, _ in get_unit_type(self.type_id).orientations()]
        if len(options) > 1:
            current = self.comp.orientation or options[0]
            index = options.index(current) if current in options else 0
            self.comp.orientation = options[(index + 1) % len(options)]
            self._refresh_geometry()

    def key_press(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.view.set_tool("select")
            return True
        return False
