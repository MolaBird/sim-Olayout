"""Snapping rules for placing components and picking points.

* Hole-mounted units (a PH2 screwed into the table) always land on a hole.
* Pedestal units and sources move freely; their optic centre snaps onto a
  point where two beams cross (where a beamsplitter combines them), else
  onto a nearby beam (so a mirror drops right into an existing beam),
  otherwise their post axis snaps to a fine grid.  Holding Alt disables
  snapping.
* Measurement points snap to holes, post axes, optic centres and beams.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import numpy as np

from olayout.catalog.units import UNIT_TYPES, Mounting, get_unit_type
from olayout.core.geometry import Pose
from olayout.core.layout import Component
from olayout.optics.tracer import TraceResult
from olayout.ui.document import LayoutDocument

SNAP_PX = 12.0  # snap radius in screen pixels


@dataclass(frozen=True)
class SnapResult:
    x: float
    y: float
    kind: str  # "hole" | "crossing" | "beam" | "grid" | "point" | "free"
    marker: tuple[float, float] | None = None


def nearest_crossing(trace: TraceResult, point, radius: float) -> tuple[float, float] | None:
    """The closest point within ``radius`` of ``point`` where two beams cross.

    Only real crossings count: a beam meeting the beam it came from (at a
    mirror or splitter) is not one.
    """
    segments = trace.segments
    if len(segments) < 2:
        return None
    p = np.asarray(point, dtype=float)
    starts = np.array([seg.start for seg in segments])
    dirs = np.array([seg.direction for seg in segments])
    lengths = np.array([seg.length for seg in segments])
    along = np.clip(np.einsum("ij,ij->i", p - starts, dirs), 0.0, lengths)
    near = np.flatnonzero(np.hypot(*(p - (starts + dirs * along[:, None])).T) <= radius)
    best: tuple[float, tuple[float, float]] | None = None
    for i, j in combinations(near, 2):
        a, b = segments[i], segments[j]
        if a.parent == b.index or b.parent == a.index or (a.parent is not None and a.parent == b.parent):
            continue
        det = dirs[i][0] * dirs[j][1] - dirs[i][1] * dirs[j][0]
        if abs(det) < 1e-6:
            continue
        w = starts[j] - starts[i]
        t = (w[0] * dirs[j][1] - w[1] * dirs[j][0]) / det
        u = (w[0] * dirs[i][1] - w[1] * dirs[i][0]) / det
        if not (0.0 <= t <= lengths[i] and 0.0 <= u <= lengths[j]):
            continue
        q = starts[i] + t * dirs[i]
        distance = float(np.hypot(*(q - p)))
        if distance <= radius and (best is None or distance < best[0]):
            best = (distance, (float(q[0]), float(q[1])))
    return None if best is None else best[1]


@dataclass
class SnapSettings:
    enabled: bool = True
    grid: float = 0.125  # fine grid for free-standing units, inches
    rotation_step: float = 15.0  # degrees


class Snapper:
    def __init__(self, doc: LayoutDocument, settings: SnapSettings):
        self.doc = doc
        self.settings = settings

    # ----------------------------------------------------------- components
    def optic_offset(self, comp: Component) -> tuple[float, float] | None:
        """Optic centre in the unit frame (None for units without optics)."""
        placed = self.doc.analysis.units.get(comp.id)
        build = placed.build if placed is not None else self.doc.analyzer.cache.build(comp)
        if build.elements:
            return build.elements[0].center
        if build.emitters:
            return build.emitters[0].position
        return None

    def component(
        self,
        comp: Component,
        raw_x: float,
        raw_y: float,
        scale: float,
        beams: TraceResult | None,
        disabled: bool = False,
    ) -> SnapResult:
        mounting = get_unit_type(comp.type).mounting
        board = self.doc.layout.breadboard
        if mounting is Mounting.HOLE:
            hx, hy = board.nearest_hole(raw_x, raw_y)
            return SnapResult(hx, hy, "hole", (hx, hy))
        if disabled or not self.settings.enabled:
            return SnapResult(raw_x, raw_y, "free")

        offset = self.optic_offset(comp)
        if beams is not None and beams.segments and offset is not None:
            center = Pose(raw_x, raw_y, comp.angle).apply(offset)
            crossing = nearest_crossing(beams, center, SNAP_PX / scale)
            if crossing is not None:
                (cx, cy), (ox, oy) = crossing, center
                return SnapResult(raw_x + cx - ox, raw_y + cy - oy, "crossing", crossing)
            hit = beams.nearest(center, SNAP_PX / scale)
            if hit is not None:
                seg, s, _ = hit
                px, py = seg.point_at(s)
                return SnapResult(raw_x + px - center[0], raw_y + py - center[1], "beam", (px, py))

        step = self.settings.grid
        return SnapResult(round(raw_x / step) * step, round(raw_y / step) * step, "grid")

    def rotation(self, angle: float, free: bool, fine: bool) -> float:
        if free:
            return round(angle, 2)
        step = 1.0 if fine else self.settings.rotation_step
        return round(angle / step) * step

    # --------------------------------------------------------------- points
    def point(self, raw_x: float, raw_y: float, scale: float, disabled: bool = False) -> SnapResult:
        """Snap a free point (measurements, probes) to something meaningful."""
        if disabled:
            return SnapResult(raw_x, raw_y, "free")
        radius = SNAP_PX / scale
        candidates: list[tuple[float, str, float, float]] = []
        board = self.doc.layout.breadboard
        hx, hy = board.nearest_hole(raw_x, raw_y)
        candidates.append((math.hypot(hx - raw_x, hy - raw_y), "hole", hx, hy))
        for comp in self.doc.layout.components:
            candidates.append((math.hypot(comp.x - raw_x, comp.y - raw_y), "point", comp.x, comp.y))
            end = member_end(comp)
            if end is not None:  # the far end of a rail or board
                candidates.append((math.hypot(end[0] - raw_x, end[1] - raw_y), "point", *end))
            placed = self.doc.analysis.units.get(comp.id)
            if placed is not None:
                for element in placed.build.elements:
                    cx, cy = comp.pose.apply(element.center)
                    candidates.append((math.hypot(cx - raw_x, cy - raw_y), "point", float(cx), float(cy)))
        best = min(candidates, key=lambda c: c[0])
        if best[0] <= radius:
            return SnapResult(best[2], best[3], best[1], (best[2], best[3]))
        hit = self.doc.analysis.trace.nearest((raw_x, raw_y), radius)
        if hit is not None:
            seg, s, _ = hit
            px, py = seg.point_at(s)
            return SnapResult(px, py, "beam", (px, py))
        return SnapResult(raw_x, raw_y, "free")


def member_end(comp: Component) -> tuple[float, float] | None:
    """World position of the far end of a straight member (a rail, a board)."""
    unit = UNIT_TYPES.get(comp.type)
    length = unit.member_length(comp.params) if unit is not None else None
    if length is None:
        return None
    x, y = comp.pose.apply((length, 0.0))
    return float(x), float(y)


class MemberSnapper:
    """Where the ends of a rail or board being drawn go: onto holes and the
    ends of other members, else onto a 1/8" grid (the far end: at 15° steps
    from the first, its length in 1/8" steps)."""

    GRID = 0.125

    def __init__(self, snapper: Snapper):
        self.snapper = snapper

    def start(self, raw_x: float, raw_y: float, scale: float, disabled: bool) -> SnapResult:
        snap = self.snapper.point(raw_x, raw_y, scale, disabled)
        if snap.kind in ("hole", "point") or disabled:
            return snap
        g = self.GRID
        return SnapResult(round(raw_x / g) * g, round(raw_y / g) * g, "grid")

    def end(self, start: tuple[float, float], raw_x: float, raw_y: float, scale: float, disabled: bool,
            free_angle: bool) -> SnapResult:
        snap = self.snapper.point(raw_x, raw_y, scale, disabled)
        if snap.kind in ("hole", "point") or disabled:
            return snap
        dx, dy = raw_x - start[0], raw_y - start[1]
        length = round(math.hypot(dx, dy) / self.GRID) * self.GRID
        angle = math.atan2(dy, dx)
        if not free_angle:
            angle = math.radians(round(math.degrees(angle) / 15.0) * 15.0)
        return SnapResult(start[0] + length * math.cos(angle), start[1] + length * math.sin(angle), "grid")


def constrain_angle(ax: float, ay: float, bx: float, by: float, step_deg: float = 45.0) -> tuple[float, float]:
    """Constrain point b so the segment a-b is at a multiple of ``step_deg``."""
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length == 0:
        return bx, by
    angle = round(math.degrees(math.atan2(dy, dx)) / step_deg) * step_deg
    rad = math.radians(angle)
    return ax + length * math.cos(rad), ay + length * math.sin(rad)


def aim_angle(incoming: tuple[float, float], hit: tuple[float, float], target: tuple[float, float]) -> float | None:
    """Mirror-normal angle that reflects a beam arriving along ``incoming`` at
    ``hit`` toward ``target`` (degrees), or None if undefined."""
    d = np.asarray(incoming, dtype=float)
    u = np.asarray(target, dtype=float) - np.asarray(hit, dtype=float)
    norm = np.hypot(*u)
    if norm < 1e-6:
        return None
    n = u / norm - d
    if np.hypot(*n) < 1e-9:
        return None
    return math.degrees(math.atan2(n[1], n[0]))
