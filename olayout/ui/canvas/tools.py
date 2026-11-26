"""Interaction tools for the canvas (select/move/rotate, measure, probe, place
and put mounts on posts, combine two beams with a beamsplitter, aim a beam at
a point, turn a clamp, drill holes).

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
from olayout.catalog.units import (
    BARE_POST,
    BASE_KIND_FIELD,
    CLAMP_ANGLE_FIELD,
    KB1X1_PRESENT,
    KB1X1_SEATED,
    Mounting,
    clamp_of,
    default_fork,
    get_unit_type,
    has_kb1x1,
    is_clamped,
    library_entry,
    mount_on_post_params,
    mounting_of,
    new_component_params,
    set_param,
    stands_on_post,
)
from olayout.core.layout import Component
from olayout.core.units import normalize_angle
from olayout.engine.aim import (
    AimError,
    AimPlan,
    AimRoute,
    aim_route,
    beam_key,
    find_beam,
    leaving_component,
    plan_aim,
    turn_toward,
)
from olayout.engine.analysis import Analyzer
from olayout.engine.combine import CombineError, CombinePlan, plan_combiner, splitter_element
from olayout.engine.fork import hole_toward, reachable_holes, resolve_fork
from olayout.engine.slots import resolve_slots
from olayout.optics.tracer import BeamSegment, TraceResult
from olayout.ui.canvas.items import Z_OVERLAY, ComponentLayerItem, MeasurementItem, cosmetic_pen, to_scene
from olayout.ui.canvas.snapping import SNAP_PX, MemberSnapper, SnapResult, constrain_angle
from olayout.ui.theme import C

if TYPE_CHECKING:
    from olayout.ui.canvas.view import LayoutView

log = logging.getLogger(__name__)

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
DRAG_THRESHOLD_PX = 4.0
ROTATE_MAGNET_PX = 6.0  # the rotation handle is pulled onto 15° steps within this many pixels
ATTACH_RADIUS = 0.4  # inches: a mount dropped this close to a bare post goes onto it
DRILL_RADIUS = 0.35  # inches: the cursor this close to a cell centre (or a drilled hole) targets it
SNAP_COLORS = {"hole": C.ACCENT, "beam": C.PROBE, "crossing": C.MEASURE}  # snap-indicator ring per snap kind
TARGET_COLORS = {"hole": C.ACCENT, "point": C.MEASURE, "grid": C.PROBE, "free": C.TEXT}  # where a beam is aimed


def dashed_line_item(color: QColor) -> QGraphicsPathItem:
    """A dashed overlay line (a picked beam, a preview), hidden until given a path."""
    item = QGraphicsPathItem()
    item.setPen(cosmetic_pen(color, 2.0, Qt.PenStyle.DashLine))
    item.setZValue(Z_OVERLAY - 1)
    item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    item.setVisible(False)
    return item


def line_path(*points: tuple[float, float]) -> QPainterPath:
    """A polyline through model points."""
    path = QPainterPath(to_scene(*points[0]))
    for point in points[1:]:
        path.lineTo(to_scene(*point))
    return path


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
        self.aim: tuple[BeamSegment, TraceResult] | None = None  # the beam Alt + handle aims
        self.band: QGraphicsRectItem | None = None
        self.band_base: list[str] = []

    # ------------------------------------------------------------ helpers
    def _on_handle(self, scene_pos: QPointF) -> bool:
        overlay = self.scene.selection_overlay
        if not overlay.active:
            return False
        handle = overlay.handle_pos(self.scale)
        return math.hypot(handle.x() - scene_pos.x(), handle.y() - scene_pos.y()) * self.scale <= 9.0

    def _outgoing_beam(self, comp_id: str):
        """The beam Alt + handle aims at the cursor: the first beam the component
        reflects, else the first that leaves it (e.g. a source's)."""
        trace = self.doc.analysis.trace
        leaving = [s for s in trace.segments if leaving_component(trace, s) == comp_id]
        reflected = [s for s in leaving if beam_key(trace, s).steps[-1:] == ((comp_id, "reflected"),)]
        chosen = (reflected or leaving or [None])[0]
        return None if chosen is None else (chosen, trace)

    # -------------------------------------------------------------- events
    def press(self, ev, scene_pos, mx, my):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        mods = ev.modifiers()
        if self._on_handle(scene_pos):
            comp = self.doc.selected_components()[0]
            self.mode = "rotate"
            self.start_poses = {comp.id: (comp.x, comp.y, comp.angle)}
            self.aim = self._outgoing_beam(comp.id)
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
            self.scene.hover_label.setVisible(False)
        elif mode == "band" and self.band is not None:
            self.scene.removeItem(self.band)
            self.band = None

    def key_press(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            if self.mode in ("drag", "rotate"):
                self.doc.cancel_interaction()
                self.scene.snap_indicator.setVisible(False)
                self.scene.hover_label.setVisible(False)
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
        self.lock_to_holes = any(mounting_of(c) in (Mounting.HOLE, Mounting.SLOTS) for c in comps)
        self.scene.hover_label.setVisible(False)

    def _drag_to(self, mx: float, my: float, mods) -> None:
        anchor = self.doc.layout.component(self.anchor_id)
        if anchor is None or self.anchor_id not in self.start_poses:
            return
        ax0, ay0, _ = self.start_poses[self.anchor_id]
        raw_x = ax0 + mx - self.press_model[0]
        raw_y = ay0 + my - self.press_model[1]
        snap = self.view.snapper.component(
            anchor, raw_x, raw_y, self.scale, self.beam_targets, disabled=bool(mods & ALT), grid=bool(mods & CTRL)
        )
        dx, dy = snap.x - ax0, snap.y - ay0
        anchor_on_holes = mounting_of(anchor) in (Mounting.HOLE, Mounting.SLOTS)
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
        if mods & ALT and self.aim is not None:  # turn so the beam passes the cursor exactly
            segment, trace = self.aim
            angle = turn_toward(comp, segment, (mx, my), trace, self.doc.analyzer.cache)
            if angle is None:
                return  # out of reach: stay put
        if angle is None:
            raw = math.degrees(math.atan2(my - y0, mx - x0))
            reach_px = max(math.hypot(mx - x0, my - y0) * self.scale, 1.0)
            magnet = min(3.0, max(1.0, math.degrees(ROTATE_MAGNET_PX / reach_px)))
            angle = self.view.snapper.rotation(raw, free=bool(mods & SHIFT), fine=bool(mods & CTRL), magnet=magnet)
        comp.angle = round(angle % 360.0, 6)
        self.doc.interaction_changed({cid})
        label = self.scene.hover_label
        label.set_text(f"{normalize_angle(comp.angle):.1f}°")
        label.setPos(to_scene(mx, my) + QPointF(14.0 / self.scale, -14.0 / self.scale))
        label.setVisible(True)

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
    """Place a unit where you click.  A mount (a mirror, a splitter, a
    collimator...) clicked onto a bare post goes onto that post; elsewhere it
    comes on a post of its own.  A KB1X1 clicked onto a post goes under its
    mount.  A straight member (a rail, a board) is drawn instead: press at one
    end, release at the other; the tool stays on for the next one until Esc
    or a right-click.

    ``move_from``: carry the mount of that component (and its settings) to
    another post, leaving its post behind."""

    name = "place"
    cursor = Qt.CursorShape.CrossCursor
    MIN_DRAWN = 0.25  # inches: a shorter drag is a click, which places the default length

    def __init__(self, view: LayoutView, key: str, angle: float = 0.0, move_from: str | None = None):
        super().__init__(view)
        source = self.doc.layout.component(move_from) if move_from else None
        self.entry = library_entry(source.type if source is not None else key)
        self.type_id = self.entry.type_id
        self.move_from = move_from if source is not None else None
        if source is not None:
            self.type_id = source.type
            params = copy.deepcopy(source.params)
            set_param(params, KB1X1_PRESENT.key, False)  # the KB1X1 stays on the old post
            set_param(params, CLAMP_ANGLE_FIELD.key, None)
            angle, orientation, label = source.angle, source.orientation, source.label
        else:
            params, orientation, label = self.entry.initial_params(), "", ""
        self.own_params = params  # what it is when not on a post
        self.angle = angle
        self.comp = Component(
            id="__ghost__", type=self.type_id, angle=angle, label=label, orientation=orientation, params=copy.deepcopy(params)
        )
        self.post_id: str | None = None  # the post it would go onto
        self.items: dict[Layer, ComponentLayerItem] = {}
        self.has_position = False
        unit = get_unit_type(self.type_id)
        self.length_key = unit.length_key
        self.members = MemberSnapper(view.snapper)
        self.drawing_from: tuple[float, float] | None = None  # the first end, while a member is drawn
        if self.length_key:
            default = unit.member_length(self.comp.params)
            self.hint = (f"Draw a {unit.name.split(',')[0].lower()}: drag from one end to the other "
                         f"(click: {default:g}\" long · Shift: any angle · Alt: no snapping · Esc: done)")
        elif source is not None:
            self.hint = f"Move {label or 'the mount'}: click a bare post, or anywhere for a new post (Esc: cancel)"
        elif self.entry.kb1x1_only:
            self.hint = "KB1X1: click a post to put it under its mount, or anywhere for a new post with one"
        elif self.entry.mounts_on_posts:
            self.hint = (f"{self.entry.name}: click a bare post to put it on, or anywhere on a new post "
                         "(Q/E rotate · F orientation · Shift-click: keep placing · Esc)")

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
        fork = screws = None
        mounting = mounting_of(self.comp)
        if mounting is Mounting.PEDESTAL:
            if self.has_position:
                fork = resolve_fork(self.comp, self.doc.layout.breadboard, [])
            else:
                fork = default_fork(clamp_of(self.comp))
        elif mounting is Mounting.SLOTS and self.has_position:
            screws = resolve_slots(self.comp, get_unit_type(self.comp.type).slots, self.doc.layout.breadboard)
        placed = self.doc.analyzer.cache.place(self.comp, fork, screws=screws)
        for item in self.items.values():
            item.set_geometry(placed)
            item.set_pose(self.comp.x, self.comp.y, self.comp.angle)

    # ------------------------------------------------------------ posts
    def _post_near(self, mx: float, my: float) -> Component | None:
        """The post this would go onto, dropped at (mx, my)."""
        if not (self.entry.mounts_on_posts or self.move_from):
            return None
        best, best_d = None, ATTACH_RADIUS
        for comp in self.doc.layout.components:
            if comp.id == self.move_from or not stands_on_post(comp.type):
                continue
            fits = not has_kb1x1(comp) if self.entry.kb1x1_only and not self.move_from else comp.type == BARE_POST
            d = math.hypot(comp.x - mx, comp.y - my)
            if fits and d <= best_d:
                best, best_d = comp, d
        return best

    def _go_onto(self, post: Component | None) -> None:
        """Make the ghost what it would be on ``post`` (None: on a post of its own)."""
        self.post_id = post.id if post is not None else None
        ghost = self.comp
        if post is None:
            ghost.type, ghost.params, ghost.angle = self.type_id, copy.deepcopy(self.own_params), self.angle
            return
        ghost.x, ghost.y = post.x, post.y
        if self.entry.kb1x1_only and not self.move_from:  # the post as it would be, with a KB1X1 under its mount
            ghost.type, ghost.angle, ghost.orientation = post.type, post.angle, post.orientation
            ghost.params = copy.deepcopy(post.params)
            set_param(ghost.params, KB1X1_PRESENT.key, True)
            set_param(ghost.params, KB1X1_SEATED.key, True)
        else:
            ghost.type, ghost.angle = self.type_id, self.angle
            ghost.params = mount_on_post_params(post, self.type_id, self.own_params)

    def move_to(self, mx: float, my: float, mods=Qt.KeyboardModifier.NoModifier) -> None:
        post = self._post_near(mx, my)
        self._go_onto(post)
        if post is not None:
            self.scene.snap_indicator.show_at(post.x, post.y, color=C.OK)
            self.view.show_hint(f"Release / click: onto {post.label or 'the post'}")
        else:
            snap = self.view.snapper.component(
                self.comp, mx, my, self.scale, self.doc.analysis.trace, disabled=bool(mods & ALT), grid=bool(mods & CTRL)
            )
            self.comp.x, self.comp.y = snap.x, snap.y
            self.show_snap(snap)
            if self.hint:
                self.view.show_hint(self.hint)
        self.has_position = True
        self._refresh_geometry()
        for item in self.items.values():
            item.setVisible(item.has_content())

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
        if self.move_from or not ev.modifiers() & SHIFT:
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

    def place(self) -> str | None:
        """Put it down where the ghost is; returns the id of the component it went into."""
        doc, ghost = self.doc, self.comp
        if self.move_from:
            at = (ghost.x, ghost.y, ghost.angle)
            return doc.move_mount(self.move_from, self.post_id, at=at)
        own = copy.deepcopy(self.own_params)  # the ghost keeps changing: never share its dicts
        if self.post_id is not None:
            if self.entry.kb1x1_only:
                doc.set_kb1x1([self.post_id], True)
                doc.set_selection([self.post_id])
            else:
                doc.mount_on_post(self.post_id, self.type_id, own, orientation=ghost.orientation, angle=ghost.angle)
            return self.post_id
        return doc.add_component(self.type_id, ghost.x, ghost.y, ghost.angle, params=own, orientation=ghost.orientation)

    # ------------------------------------------------- drawing a straight member
    def _no_snap(self, mods) -> bool:
        return bool(mods & ALT) or not self.view.snap_settings.enabled

    def _set_length(self, length: float) -> None:
        set_param(self.comp.params, self.length_key, round(length, 6))
        set_param(self.own_params, self.length_key, round(length, 6))
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
            self.comp.angle = self.angle = math.degrees(math.atan2(dy, dx)) % 360.0
            self._set_length(length)
        self.show_snap(snap)
        return length

    # Rotation / orientation keys arrive through the main window's actions.
    def rotate(self, delta: float) -> None:
        self.angle = round((self.angle + delta) % 360.0, 6)
        if self.comp.type == self.type_id:  # not the post a KB1X1 is going onto
            self.comp.angle = self.angle
        self._refresh_geometry()

    def cycle_orientation(self) -> None:
        options = [name for name, _ in get_unit_type(self.type_id).orientations()]
        if len(options) > 1 and self.comp.type == self.type_id:
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
    NEW_SPLITTER = "pellicle-km100bp"  # on a KB1X1, on a clamped post (it can stand anywhere)
    PREVIEW_LENGTH = 2.5  # inches of the combined beam drawn beyond the splitter

    def __init__(self, view: LayoutView):
        super().__init__(view)
        target = self._selected_splitter()
        self.existing = target is not None
        self.target = target or Component(id="__combine__", type=self.NEW_SPLITTER, params=self._new_splitter_params())
        self.others: list = []
        self.trace = TraceResult()
        self.first: BeamSegment | None = None
        self.plans: dict[tuple[int, int], CombinePlan | CombineError] = {}
        self._retrace()
        self.ghost: dict[Layer, ComponentLayerItem] = {}
        self.first_line = dashed_line_item(C.ACCENT)
        self.output_line = dashed_line_item(C.PROBE)
        self._listening = False
        self.hint = self._step_hint()

    @classmethod
    def _new_splitter_params(cls) -> dict:
        params = new_component_params(cls.NEW_SPLITTER)
        set_param(params, BASE_KIND_FIELD.key, "clamped")
        set_param(params, KB1X1_PRESENT.key, True)
        return params

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
        self.first_line.setPath(line_path(seg.start, seg.end))
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
        if mounting_of(ghost) is Mounting.PEDESTAL:
            fork = resolve_fork(ghost, self.doc.layout.breadboard, [])
        placed = self.doc.analyzer.cache.place(ghost, fork)
        for item in self.ghost.values():
            item.set_geometry(placed)
            item.set_pose(ghost.x, ghost.y, ghost.angle)
            item.setVisible(item.has_content())
        (px, py), (dx, dy) = plan.point, plan.direction
        self.output_line.setPath(line_path((px, py), (px + self.PREVIEW_LENGTH * dx, py + self.PREVIEW_LENGTH * dy)))
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


# ==========================================================================
# Aim a beam at a point
# ==========================================================================
class AimTool(Tool):
    """Click a beam, then the point it should pass through: one component
    turns about its post so it does, exactly.  The point snaps to holes and
    optic centres, else to the 1/8" grid (Alt: anywhere).

    The component that turns is ``comp_id`` (the selected one) if given: the
    beam may then be any that comes from it, also after further flat mirrors,
    splitters and windows, which stay put.  Otherwise it is the component the
    clicked beam leaves.  With ``comp_id`` and a single beam to choose from,
    that beam is picked straight away."""

    name = "aim"
    cursor = Qt.CursorShape.CrossCursor
    PAST_TARGET = 1.0  # inches of the aimed beam previewed beyond the point

    def __init__(self, view: LayoutView, comp_id: str | None = None):
        super().__init__(view)
        self.only = comp_id
        self.trace = self.doc.analysis.trace
        self.picked: BeamSegment | None = None
        self.route: AimRoute | None = None  # how the picked beam comes from the component that turns
        self.routes: dict[int, AimRoute | AimError] = {}  # per segment index of the current trace
        self.plans: dict[tuple[float, float], AimPlan | AimError] = {}
        self.beam_line = dashed_line_item(C.ACCENT)
        self.preview_line = dashed_line_item(C.PROBE)
        self.ghost: dict[Layer, ComponentLayerItem] = {}
        self._listening = False
        choices = [seg for seg in self.trace.segments if isinstance(self._route(seg), AimRoute)] if comp_id else []
        self._auto_picked = len(choices) == 1
        if self._auto_picked:
            self.picked, self.route = choices[0], self._route(choices[0])
        self.hint = self._step_hint()

    # ------------------------------------------------------------- helpers
    def _route(self, seg: BeamSegment) -> AimRoute | AimError:
        """How ``seg`` comes from the component that would turn, or why it can't be aimed."""
        if seg.index not in self.routes:
            comp = self.doc.layout.component(self.only or leaving_component(self.trace, seg))
            if comp is None or get_unit_type(comp.type).structural:
                self.routes[seg.index] = AimError("This beam does not come from a part that can turn")
            else:
                others = [u for cid, u in self.doc.analysis.units.items() if cid != comp.id]
                try:
                    self.routes[seg.index] = aim_route(comp, seg, self.trace, others, self.doc.layout)
                except AimError as exc:
                    self.routes[seg.index] = exc
        return self.routes[seg.index]

    def _turned(self) -> Component | None:
        return self.doc.layout.component(self.route.component_id) if self.route is not None else None

    def _beam_name(self, seg: BeamSegment, route: AimRoute) -> str:
        """E.g. "M1's reflected beam", or "the beam after M3, turning M1"."""
        comp = self.doc.layout.component(route.component_id)
        name = comp.label or comp.id
        if route.surfaces:
            return f"the beam after {route.surfaces[-1].name}, turning {name}"
        steps = beam_key(self.trace, seg).steps
        port = f"{steps[-1][1]} beam" if steps and steps[-1][0] == comp.id else "beam"
        return f"{name}'s {port}"

    @staticmethod
    def _route_points(route: AimRoute) -> list[tuple[float, float]]:
        """The beam from where it leaves the turning component to the end of the picked segment."""
        return [seg.start for seg in route.segments] + [route.segments[-1].end]

    def _step_hint(self) -> str:
        if self.picked is None:
            if self.only:
                name = self.doc.layout.component(self.only).label or "the selected part"
                return (f"Aim with {name}: click a beam coming from it, directly or after flat mirrors, "
                        "splitters and windows  (Esc: back)")
            return "Aim a beam: click the beam where it leaves a component (select a part first to turn that one)"
        return (f"Aim {self._beam_name(self.picked, self.route)}: click the point it should pass through "
                "(holes and optic centres snap, else the 1/8\" grid · Alt: anywhere · Esc: back)")

    def _show_step(self) -> None:
        self.hint = self._step_hint()
        self.view.show_hint(self.hint)

    # ----------------------------------------------------------- lifecycle
    def activate(self) -> None:
        super().activate()
        for item in (self.beam_line, self.preview_line):
            if item.scene() is None:
                self.scene.addItem(item)
        if not self.ghost:
            for layer in Layer:
                item = ComponentLayerItem("__aim_ghost__", layer, ghost=True)
                item.setVisible(False)
                self.scene.addItem(item)
                self.ghost[layer] = item
        if self.picked is not None:
            self._pick(self.picked, self.route)
        if not self._listening:
            self.doc.analysisUpdated.connect(self._layout_changed)
            self._listening = True

    def deactivate(self) -> None:
        super().deactivate()
        self._stop_listening()
        for item in (self.beam_line, self.preview_line, *self.ghost.values()):
            if item.scene() is not None:
                self.scene.removeItem(item)
        self.ghost.clear()

    def _stop_listening(self) -> None:
        if self._listening:
            self.doc.analysisUpdated.disconnect(self._layout_changed)
            self._listening = False

    def _layout_changed(self) -> None:
        """Undo, redo or another edit: follow the picked beam if it is still there."""
        key = beam_key(self.trace, self.picked) if self.picked is not None else None
        self.trace = self.doc.analysis.trace
        self.routes.clear()
        self.plans.clear()
        if self.only and self.doc.layout.component(self.only) is None:
            self.view.set_tool("select")  # the part that would turn is gone
            return
        if key is not None:
            seg = find_beam(self.trace, key)
            route = self._route(seg) if seg is not None else None
            if isinstance(route, AimRoute):
                self._pick(seg, route)
            else:
                self._forget()
                self._show_step()

    # -------------------------------------------------------------- events
    def move(self, ev, scene_pos, mx, my):
        label = self.scene.hover_label
        if self.picked is None:
            hit = self.trace.nearest((mx, my), SNAP_PX / self.scale)
            if hit is None:
                self.scene.snap_indicator.setVisible(False)
                self.beam_line.setVisible(False)
                label.setVisible(False)
                return
            seg, along = hit[0], hit[1]
            route = self._route(seg)
            ok = isinstance(route, AimRoute)
            self.scene.snap_indicator.show_at(*seg.point_at(along), color=C.ACCENT if ok else C.ERROR)
            if ok:  # the whole beam that would be aimed, from the part that turns
                self.beam_line.setPath(line_path(*self._route_points(route)))
            self.beam_line.setVisible(ok)
            label.set_text(f"Aim {self._beam_name(seg, route)}" if ok else f"⚠ {route}")
        else:
            comp = self._turned()
            snap = self.view.snapper.target(mx, my, self.scale, disabled=bool(ev.modifiers() & ALT), exclude=comp.id)
            self.scene.snap_indicator.show_at(snap.x, snap.y, color=TARGET_COLORS.get(snap.kind, C.TEXT))
            plan = self._plan((snap.x, snap.y))
            self._show_plan(plan if isinstance(plan, AimPlan) else None)
            label.set_text(plan.summary(comp.label) if isinstance(plan, AimPlan) else f"⚠ {plan}")
        label.setPos(scene_pos + QPointF(12.0 / self.scale, -12.0 / self.scale))
        label.setVisible(True)

    def press(self, ev, scene_pos, mx, my):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self.picked is None:
            hit = self.trace.nearest((mx, my), SNAP_PX / self.scale)
            if hit is None:
                return
            route = self._route(hit[0])
            if isinstance(route, AimRoute):
                self._pick(hit[0], route)
            else:
                self.view.notify(f"⚠ {route}")
            return
        comp = self._turned()
        snap = self.view.snapper.target(mx, my, self.scale, disabled=bool(ev.modifiers() & ALT), exclude=comp.id)
        plan = self._plan((snap.x, snap.y))
        if isinstance(plan, AimError):
            self.view.notify(f"⚠ {plan}")
            return
        self._stop_listening()
        self.doc.transform_components({comp.id: (comp.x, comp.y, plan.angle)}, f"Aim with {comp.label or 'a part'}")
        self.doc.set_selection([comp.id])
        self.view.set_tool("select")
        self.view.notify(plan.summary(comp.label))

    def key_press(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.cancel()
            return True
        return False

    def context_click(self) -> bool:
        self.cancel()
        return True

    def cancel(self) -> None:
        """Esc / right-click: forget the picked beam, or leave the tool."""
        if self.picked is None or self._auto_picked:
            self.view.set_tool("select")
            return
        self._forget()
        self._show_step()

    # ------------------------------------------------------------ planning
    def _pick(self, seg: BeamSegment, route: AimRoute) -> None:
        self.picked, self.route = seg, route
        self.plans.clear()
        self.beam_line.setPath(line_path(*self._route_points(route)))
        self.beam_line.setVisible(True)
        self._show_step()

    def _forget(self) -> None:
        self.picked = self.route = None
        self.beam_line.setVisible(False)
        self._show_plan(None)
        self.scene.hover_label.setVisible(False)

    def _plan(self, target: tuple[float, float]) -> AimPlan | AimError:
        key = (round(target[0], 6), round(target[1], 6))
        if key not in self.plans:
            comp = self._turned()
            others = [u for cid, u in self.doc.analysis.units.items() if cid != comp.id]
            try:
                self.plans[key] = plan_aim(
                    comp, self.picked, target, self.trace, others, self.doc.layout, self.doc.analyzer.cache
                )
            except AimError as exc:
                self.plans[key] = exc
            except Exception:  # never let a solver bug take the canvas down
                log.exception("aiming a beam failed")
                self.plans[key] = AimError("Could not aim the beam there (see the log)")
        return self.plans[key]

    def _show_plan(self, plan: AimPlan | None) -> None:
        """Ghost of the component turned, and the beam it then sends through the point."""
        if plan is None:
            for item in (*self.ghost.values(), self.preview_line):
                item.setVisible(False)
            return
        ghost = Component.from_dict(self.doc.layout.component(plan.component_id).to_dict())
        ghost.angle = plan.angle
        fork = resolve_fork(ghost, self.doc.layout.breadboard, []) if mounting_of(ghost) is Mounting.PEDESTAL else None
        placed = self.doc.analyzer.cache.place(ghost, fork)
        for item in self.ghost.values():
            item.set_geometry(placed)
            item.set_pose(ghost.x, ghost.y, ghost.angle)
            item.setVisible(item.has_content())
        (dx, dy), (tx, ty) = plan.direction, plan.target
        self.preview_line.setPath(line_path(*plan.path[:-1], (tx + self.PAST_TARGET * dx, ty + self.PAST_TARGET * dy)))
        self.preview_line.setVisible(True)


# ==========================================================================
# Turn a clamp
# ==========================================================================
class ClampTool(Tool):
    """Point a clamped post's clamping fork at the hole you choose: the holes
    its screw can reach are ringed, and the fork follows the cursor from one
    to the next.  Click to keep it; Esc or a right-click cancels."""

    name = "clamp"
    cursor = Qt.CursorShape.CrossCursor
    MARK_R = 0.17  # inches: the rings around reachable holes

    def __init__(self, view: LayoutView, comp_id: str):
        super().__init__(view)
        self.comp_id = comp_id
        comp = self.doc.layout.component(comp_id)
        name = (comp.label if comp is not None else "") or "this post"
        self.hint = f"Turn the clamp of {name}: point at one of the ringed holes and click (Esc: cancel)"
        self.marks = QGraphicsPathItem()
        self.marks.setPen(cosmetic_pen(C.OK, 1.4, Qt.PenStyle.DashLine))
        self.marks.setZValue(Z_OVERLAY - 1)
        self.marks.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def _comp(self) -> Component | None:
        comp = self.doc.layout.component(self.comp_id)
        return comp if comp is not None and is_clamped(comp) else None

    def activate(self) -> None:
        super().activate()
        comp = self._comp()
        if comp is None:
            return
        if self.marks.scene() is None:
            self.scene.addItem(self.marks)
        path = QPainterPath()
        for hx, hy in reachable_holes(comp, self.doc.layout.breadboard):
            path.addEllipse(to_scene(float(hx), float(hy)), self.MARK_R, self.MARK_R)
        self.marks.setPath(path)
        if not self.doc.interacting:
            self.doc.begin_interaction()

    def deactivate(self) -> None:
        super().deactivate()
        if self.marks.scene() is not None:
            self.scene.removeItem(self.marks)
        if self.doc.interacting:
            self.doc.cancel_interaction()

    def move(self, ev, scene_pos, mx, my):
        comp = self._comp()
        if comp is None:
            return
        hole = hole_toward(comp, self.doc.layout.breadboard, math.degrees(math.atan2(my - comp.y, mx - comp.x)))
        if hole is None:
            return
        direction = round(math.degrees(math.atan2(hole[1] - comp.y, hole[0] - comp.x)), 2)
        self.scene.snap_indicator.show_at(*hole, color=C.OK)
        set_param(comp.params, CLAMP_ANGLE_FIELD.key, normalize_angle(direction))
        self.doc.interaction_changed({comp.id})

    def press(self, ev, scene_pos, mx, my):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        self.move(ev, scene_pos, mx, my)
        self.doc.end_interaction("Turn clamp")
        self.view.set_tool("select")

    def key_press(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.view.set_tool("select")  # deactivating cancels
            return True
        return False

    def context_click(self) -> bool:
        self.view.set_tool("select")
        return True


# ==========================================================================
# Drill holes
# ==========================================================================
class DrillTool(Tool):
    """Drill extra tapped holes, only at the centres of the grid cells (where
    they clear the grid's threads); click a drilled hole to fill it in."""

    name = "drill"
    cursor = Qt.CursorShape.CrossCursor
    hint = "Drill: click the centre of a grid cell to drill a 1/4\"-20 hole there, a drilled hole to fill it (Esc: done)"

    def _target(self, mx: float, my: float) -> tuple[float, float, str | None] | None:
        """The spot under the cursor: (x, y, why it can't be drilled or None), or None."""
        board = self.doc.layout.breadboard
        drilled = min(board.drilled, key=lambda h: math.hypot(h[0] - mx, h[1] - my), default=None)
        if drilled is not None and math.hypot(drilled[0] - mx, drilled[1] - my) <= DRILL_RADIUS:
            return drilled[0], drilled[1], None
        centre = board.nearest_cell_centre(mx, my)
        if centre is None or math.hypot(centre[0] - mx, centre[1] - my) > DRILL_RADIUS * board.pitch:
            return None
        return centre[0], centre[1], board.drill_problem(*centre)

    def move(self, ev, scene_pos, mx, my):
        target = self._target(mx, my)
        label = self.scene.hover_label
        if target is None:
            self.scene.snap_indicator.setVisible(False)
            label.setVisible(False)
            return
        x, y, problem = target
        filling = self.doc.layout.breadboard.drilled_at(x, y) is not None
        color = C.MEASURE if filling else (C.ERROR if problem else C.OK)
        self.scene.snap_indicator.show_at(x, y, color=color)
        label.set_text("Fill this drilled hole" if filling else (f"⚠ {problem}" if problem else "Drill here"))
        label.setPos(to_scene(x, y) + QPointF(12.0 / self.scale, -12.0 / self.scale))
        label.setVisible(True)

    def press(self, ev, scene_pos, mx, my):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        target = self._target(mx, my)
        if target is None:
            self.view.notify("Holes can only be drilled at the centre of a grid cell")
            return
        x, y, problem = target
        if self.doc.layout.breadboard.drilled_at(x, y) is not None:
            self.doc.fill_hole(x, y)
        elif problem:
            self.view.notify(f"⚠ Cannot drill here: {problem}")
        else:
            self.doc.drill_hole(x, y)
        self.move(ev, scene_pos, mx, my)

    def key_press(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.view.set_tool("select")
            return True
        return False

    def context_click(self) -> bool:
        self.view.set_tool("select")
        return True
