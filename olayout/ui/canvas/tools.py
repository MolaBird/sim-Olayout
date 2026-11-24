"""Interaction tools for the canvas (select/move/rotate, measure, probe, place,
combine two beams with a beamsplitter).

The view forwards mouse/key events to the active tool with both the scene
position and the model position (inches, y up).
"""

from __future__ import annotations

import copy
import logging
import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainterPath
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsRectItem

from olayout.catalog.parts import Layer
from olayout.catalog.units import Mounting, default_fork, get_unit_type, new_component_params, set_param
from olayout.core.layout import Component
from olayout.engine.analysis import Analyzer
from olayout.engine.combine import CombineError, CombinePlan, plan_combiner, splitter_element
from olayout.engine.fork import resolve_fork
from olayout.optics.tracer import BeamSegment, TraceResult
from olayout.ui.canvas.items import Z_OVERLAY, ComponentLayerItem, MeasurementItem, cosmetic_pen, to_scene
from olayout.ui.canvas.snapping import SNAP_PX, MemberSnapper, SnapResult, aim_angle, constrain_angle
from olayout.ui.theme import C

if TYPE_CHECKING:
    from olayout.ui.canvas.view import LayoutView

log = logging.getLogger(__name__)

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
DRAG_THRESHOLD_PX = 4.0
SNAP_COLORS = {"hole": C.ACCENT, "beam": C.PROBE, "crossing": C.MEASURE}  # snap-indicator ring per snap kind


class Tool:
    name = "tool"
    cursor = Qt.CursorShape.ArrowCursor
    hint = ""  # what to do next, shown in the status bar ("" = just the tool's name)

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

    def context_click(self) -> bool:
        """A right-click (without dragging): True if the tool used it, else the context menu opens."""
        return False

    def leave(self) -> None:
        self.scene.hover_label.setVisible(False)
        self.scene.set_hovered(None)

    # Shared: where a moving component snapped to.
    def show_snap(self, snap: SnapResult) -> None:
        color = SNAP_COLORS.get(snap.kind)
        if snap.marker is not None and color is not None:
            self.scene.snap_indicator.show_at(*snap.marker, color=color)
        else:
            self.scene.snap_indicator.setVisible(False)

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
        """The beam a mount's optic reflects, for aiming with Alt.  Only single-optic
        units aim: their angle is the optic's normal (a beam expander's is not)."""
        placed = self.doc.analysis.units.get(comp_id)
        if placed is None or len(placed.build.elements) != 1:
            return None
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
        self.show_snap(snap)

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
    """Place a unit where you click.  A straight member (a rail, a board) is
    drawn instead: press at one end, release at the other; the tool stays on
    for the next one until Esc or a right-click."""

    name = "place"
    cursor = Qt.CursorShape.CrossCursor
    MIN_DRAWN = 0.25  # inches: a shorter drag is a click, which places the default length

    def __init__(self, view: LayoutView, type_id: str, angle: float = 0.0):
        super().__init__(view)
        self.type_id = type_id
        self.comp = Component(id="__ghost__", type=type_id, angle=angle, params=new_component_params(type_id))
        self.items: dict[Layer, ComponentLayerItem] = {}
        self.has_position = False
        unit = get_unit_type(type_id)
        self.length_key = unit.length_key
        self.members = MemberSnapper(view.snapper)
        self.drawing_from: tuple[float, float] | None = None  # the first end, while a member is drawn
        if self.length_key:
            default = unit.member_length(self.comp.params)
            self.hint = (f"Draw a {unit.name.split(',')[0].lower()}: drag from one end to the other "
                         f"(click: {default:g}\" long · Shift: any angle · Alt: no snapping · Esc: done)")

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
        self.show_snap(snap)

    def move(self, ev, scene_pos, mx, my):
        if self.drawing_from is not None:
            self._draw_to(mx, my, ev.modifiers())
        elif self.length_key:
            self._start_at(mx, my, ev.modifiers())
        else:
            self.move_to(mx, my, ev.modifiers())

    def press(self, ev, scene_pos, mx, my):
        if ev.button() == Qt.MouseButton.RightButton:
            self.view.set_tool("select")
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self.length_key:
            self._start_at(mx, my, ev.modifiers())
            self.drawing_from = (self.comp.x, self.comp.y)
            return
        self.move_to(mx, my, ev.modifiers())
        self.place()
        if not ev.modifiers() & SHIFT:
            self.view.set_tool("select")

    def release(self, ev, scene_pos, mx, my):
        if ev.button() != Qt.MouseButton.LeftButton or self.drawing_from is None:
            return
        drawn = self._draw_to(mx, my, ev.modifiers())
        self.drawing_from = None
        if drawn < self.MIN_DRAWN:  # a click: the default length, along the current angle
            self._set_length(get_unit_type(self.type_id).member_length(new_component_params(self.type_id)))
        self.place()
        self._set_length(get_unit_type(self.type_id).member_length(new_component_params(self.type_id)))
        self.view.setFocus()

    def place(self) -> str:
        return self.doc.add_component(
            self.type_id,
            self.comp.x,
            self.comp.y,
            self.comp.angle,
            params=copy.deepcopy(self.comp.params),  # the ghost keeps changing: never share its dicts
            orientation=self.comp.orientation,
        )

    # ------------------------------------------------- drawing a straight member
    def _no_snap(self, mods) -> bool:
        return bool(mods & ALT) or not self.view.snap_settings.enabled

    def _set_length(self, length: float) -> None:
        set_param(self.comp.params, self.length_key, round(length, 6))
        self._refresh_geometry()

    def _start_at(self, mx: float, my: float, mods) -> None:
        """Before the press: the member's first end follows the cursor."""
        snap = self.members.start(mx, my, self.scale, self._no_snap(mods))
        self.comp.x, self.comp.y = snap.x, snap.y
        self.has_position = True
        self._refresh_geometry()
        for item in self.items.values():
            item.setVisible(item.has_content())
        self.show_snap(snap)

    def _draw_to(self, mx: float, my: float, mods) -> float:
        """While pressed: the member runs from its first end to the cursor.  Returns its length."""
        start = self.drawing_from
        snap = self.members.end(start, mx, my, self.scale, self._no_snap(mods), free_angle=bool(mods & SHIFT))
        dx, dy = snap.x - start[0], snap.y - start[1]
        length = math.hypot(dx, dy)
        if length >= self.MIN_DRAWN:
            self.comp.angle = math.degrees(math.atan2(dy, dx)) % 360.0
            self._set_length(length)
        self.show_snap(snap)
        return length

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


# ==========================================================================
# Combine two beams with a beamsplitter
# ==========================================================================
class CombineTool(Tool):
    """Click the beam to transmit, then the beam to reflect onto it: a
    beamsplitter goes where they cross, turned and mounted so both leave as
    one beam.  Works on the selected splitter, or places a new pellicle."""

    name = "combine"
    cursor = Qt.CursorShape.CrossCursor
    NEW_SPLITTER = "mag-km-pellicle"
    PREVIEW_LENGTH = 2.5  # inches of the combined beam drawn beyond the splitter

    def __init__(self, view: LayoutView):
        super().__init__(view)
        target = self._selected_splitter()
        self.existing = target is not None
        self.target = target or Component(
            id="__combine__", type=self.NEW_SPLITTER, params=new_component_params(self.NEW_SPLITTER)
        )
        self.others: list = []
        self.trace = TraceResult()
        self.first: BeamSegment | None = None
        self.plans: dict[tuple[int, int], CombinePlan | CombineError] = {}
        self._retrace()
        self.ghost: dict[Layer, ComponentLayerItem] = {}
        self.first_line = self._path_item(C.ACCENT)
        self.output_line = self._path_item(C.PROBE)
        self._listening = False
        self.hint = self._step_hint()

    def _retrace(self) -> None:
        """The beams as they would be without the splitter being placed."""
        self.others = [u for cid, u in self.doc.analysis.units.items() if cid != self.target.id]
        self.trace = Analyzer.trace(self.others, self.doc.layout)
        self.plans.clear()

    def _selected_splitter(self) -> Component | None:
        """The selected component, if it is a single beamsplitter."""
        selected = self.doc.selected_components()
        if len(selected) != 1:
            return None
        placed = self.doc.analysis.units.get(selected[0].id)
        return selected[0] if placed is not None and splitter_element(placed.build) is not None else None

    @staticmethod
    def _path_item(color: QColor) -> QGraphicsPathItem:
        item = QGraphicsPathItem()
        item.setPen(cosmetic_pen(color, 2.0, Qt.PenStyle.DashLine))
        item.setZValue(Z_OVERLAY - 1)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        item.setVisible(False)
        return item

    def _step_hint(self) -> str:
        what = (self.target.label or "the selected splitter") if self.existing else "a new pellicle beamsplitter"
        step = "the beam to transmit" if self.first is None else "the beam to reflect onto it"
        return f"Combine beams with {what}: click {step}  (Esc: back)"

    def _show_step(self) -> None:
        self.hint = self._step_hint()
        self.view.show_hint(self.hint)

    # ------------------------------------------------------------- lifecycle
    def activate(self) -> None:
        super().activate()
        for item in (self.first_line, self.output_line):
            if item.scene() is None:
                self.scene.addItem(item)
        if not self.ghost:
            for layer in Layer:
                item = ComponentLayerItem("__combine_ghost__", layer, ghost=True)
                item.setVisible(False)
                self.scene.addItem(item)
                self.ghost[layer] = item
        if not self._listening:
            self.doc.analysisUpdated.connect(self._layout_changed)
            self._listening = True

    def deactivate(self) -> None:
        super().deactivate()
        self._stop_listening()
        for item in (self.first_line, self.output_line, *self.ghost.values()):
            if item.scene() is not None:
                self.scene.removeItem(item)
        self.ghost.clear()

    def _stop_listening(self) -> None:
        if self._listening:
            self.doc.analysisUpdated.disconnect(self._layout_changed)
            self._listening = False

    def _layout_changed(self) -> None:
        """Undo, redo or another edit while picking: re-trace, keeping the first
        beam if it is still there unchanged."""
        if self.existing:
            current = self.doc.layout.component(self.target.id)  # undo/redo replaces the objects
            if current is None:
                self.view.set_tool("select")  # the splitter itself is gone
                return
            self.target = current
        first = self.first
        self._retrace()
        if first is None:
            return
        same = next(
            (s for s in self.trace.segments if math.dist(s.start, first.start) < 1e-9
             and math.dist(s.direction, first.direction) < 1e-9),
            None,
        )
        if same is not None:
            self._pick_first(same)
        else:
            self._forget_first()
            self._show_step()

    # ---------------------------------------------------------------- events
    def _beam_at(self, mx: float, my: float) -> tuple[BeamSegment, float] | None:
        hit = self.trace.nearest((mx, my), SNAP_PX / self.scale)
        return None if hit is None else (hit[0], hit[1])

    def _plan(self, second: BeamSegment) -> CombinePlan | CombineError:
        key = (self.first.index, second.index)
        if key not in self.plans:
            try:
                self.plans[key] = plan_combiner(
                    self.target, self.first, second, self.trace, self.others, self.doc.layout, self.doc.analyzer.cache
                )
            except CombineError as exc:
                self.plans[key] = exc
            except Exception:  # never let a planning bug take the canvas down
                log.exception("planning a beam combiner failed")
                self.plans[key] = CombineError("Could not plan this combination (see the log)")
        return self.plans[key]

    def move(self, ev, scene_pos, mx, my):
        hit = self._beam_at(mx, my)
        label = self.scene.hover_label
        if hit is None:
            self.scene.snap_indicator.setVisible(False)
            label.setVisible(False)
            self._show_plan(None)
            return
        seg, s = hit
        self.scene.snap_indicator.show_at(*seg.point_at(s), color=C.ACCENT if self.first is None else C.PROBE)
        if self.first is None or seg.index == self.first.index:
            label.setVisible(False)
            self._show_plan(None)
            return
        plan = self._plan(seg)
        self._show_plan(plan if isinstance(plan, CombinePlan) else None)
        label.set_text(plan.summary() if isinstance(plan, CombinePlan) else f"⚠ {plan}")
        label.setPos(scene_pos)
        label.setVisible(True)

    def press(self, ev, scene_pos, mx, my):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._beam_at(mx, my)
        if hit is None:
            return
        seg, _ = hit
        if self.first is None or seg.index == self.first.index:
            self._pick_first(seg)
            return
        plan = self._plan(seg)
        if isinstance(plan, CombineError):
            self.view.notify(f"⚠ {plan}")
            return
        self._apply(plan)

    def key_press(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.cancel()
            return True
        return False

    def context_click(self) -> bool:
        self.cancel()
        return True

    def cancel(self) -> None:
        """Esc / right-click: forget the first beam, or leave the tool."""
        if self.first is None:
            self.view.set_tool("select")
            return
        self._forget_first()
        self._show_step()

    # --------------------------------------------------------------- helpers
    def _forget_first(self) -> None:
        self.first = None
        self.first_line.setVisible(False)
        self._show_plan(None)
        self.scene.hover_label.setVisible(False)

    def _pick_first(self, seg: BeamSegment) -> None:
        self.first = seg
        path = QPainterPath(to_scene(*seg.start))
        path.lineTo(to_scene(*seg.end))
        self.first_line.setPath(path)
        self.first_line.setVisible(True)
        self._show_step()

    def _show_plan(self, plan: CombinePlan | None) -> None:
        """Ghost of the splitter where the plan puts it, and the combined beam ahead."""
        if plan is None:
            for item in (*self.ghost.values(), self.output_line):
                item.setVisible(False)
            return
        ghost = Component.from_dict(self.target.to_dict())
        ghost.x, ghost.y, ghost.angle, ghost.orientation = plan.x, plan.y, plan.angle, plan.orientation
        fork = None
        if get_unit_type(ghost.type).mounting is Mounting.PEDESTAL:
            fork = resolve_fork(ghost, self.doc.layout.breadboard, [])
        placed = self.doc.analyzer.cache.place(ghost, fork)
        for item in self.ghost.values():
            item.set_geometry(placed)
            item.set_pose(ghost.x, ghost.y, ghost.angle)
            item.setVisible(item.has_content())
        (px, py), (dx, dy) = plan.point, plan.direction
        path = QPainterPath(to_scene(px, py))
        path.lineTo(to_scene(px + self.PREVIEW_LENGTH * dx, py + self.PREVIEW_LENGTH * dy))
        self.output_line.setPath(path)
        self.output_line.setVisible(True)

    def _apply(self, plan: CombinePlan) -> None:
        doc = self.doc
        self._stop_listening()  # our own edit: no need to start over
        if self.existing:
            target_id = self.target.id

            def mutate(layout) -> None:
                comp = layout.component(target_id)
                if comp is not None:
                    comp.x, comp.y, comp.angle, comp.orientation = plan.x, plan.y, plan.angle, plan.orientation

            doc.apply("Combine beams", mutate, changed={target_id})
            doc.set_selection([target_id])
        else:
            params = dict(self.target.params)
            doc.add_component(self.target.type, plan.x, plan.y, plan.angle, params=params, orientation=plan.orientation)
        self.view.set_tool("select")
        self.view.notify(plan.summary().replace("\n", " "))  # the status bar has one line
