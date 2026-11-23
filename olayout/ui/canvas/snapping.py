"""Snapping rules for placing components and picking points.

* Hole-mounted units (a PH2 screwed into the table) always land on a hole.
* Pedestal units and sources move freely; their optic centre snaps onto a
  nearby beam (so a mirror drops right into an existing beam), otherwise
  their post axis snaps to a fine grid.  Holding Alt disables snapping.
* Measurement points snap to holes, post axes, optic centres and beams.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from olayout.catalog.units import Mounting, get_unit_type
from olayout.core.geometry import Pose
from olayout.core.layout import Component
from olayout.optics.tracer import TraceResult
from olayout.ui.document import LayoutDocument

SNAP_PX = 12.0  # snap radius in screen pixels


@dataclass(frozen=True)
class SnapResult:
    x: float
    y: float
    kind: str  # "hole" | "beam" | "grid" | "point" | "free"
    marker: tuple[float, float] | None = None


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
