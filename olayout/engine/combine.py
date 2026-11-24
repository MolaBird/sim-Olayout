"""Smart beamsplitter placement: combine two beams into one.

Pick the beam to *transmit* (T) and the beam to *reflect onto it* (R).  The
splitter then goes where their paths cross, with its surface normal bisecting
them (n ∝ d_T − d_R), so R's reflection leaves along T's direction.

Every mounting orientation of the splitter, and either face toward R, is
tried by tracing the layout with the splitter in place.  The candidate that
sends the most power from *both* beams along the combined path wins: e.g. a
kinematic mount whose L-shaped plates would block T is turned so that T
leaves through the open side.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from olayout.catalog.units import Mounting, UnitBuild, get_unit_type
from olayout.core.geometry import Pose
from olayout.core.layout import Component, Layout
from olayout.engine.analysis import Analyzer
from olayout.engine.world import BuildCache, PlacedUnit
from olayout.optics.elements import OpticalElement, OpticKind
from olayout.optics.tracer import BeamSegment, TraceResult, WarningKind

PARALLEL_LIMIT = math.sin(math.radians(0.5))  # beams closer to parallel never usefully cross
SAME_DIRECTION = math.cos(math.radians(0.05))
POINT_TOLERANCE = 1e-4  # inches: a beam "starts at" the crossing if this close


class CombineError(ValueError):
    """The two beams cannot be combined (message is shown to the user)."""


@dataclass(frozen=True)
class CombinePlan:
    """Where to put the splitter, and what it achieves."""

    x: float  # component pose
    y: float
    angle: float
    orientation: str
    point: tuple[float, float]  # where the beams cross
    direction: tuple[float, float]  # the combined beam's direction (T's)
    transmitted: float  # mW of T leaving along the combined path
    reflected: float  # mW of R leaving along it
    off_by: float = 0.0  # hole-mounted: distance from the optic centre to the crossing
    into_mount: int = 0  # beams (the unused output port) that end on the splitter's own mount

    @property
    def combined(self) -> bool:
        return self.transmitted > 0 and self.reflected > 0

    def summary(self) -> str:
        angle = math.degrees(math.atan2(self.direction[1], self.direction[0])) % 360.0
        if not self.combined:
            lost = "transmitted" if self.transmitted <= 0 else "reflected"
            text = (
                f"Splitter at ({self.point[0]:.3f}, {self.point[1]:.3f}) in, but the {lost} beam "
                "does not leave along the combined path"
            )
        else:
            text = (
                f"Combined at ({self.point[0]:.3f}, {self.point[1]:.3f}) in, toward {angle:.1f}°: "
                f"{self.transmitted:.3g} mW transmitted + {self.reflected:.3g} mW reflected"
            )
        if self.off_by > 1e-3:
            text += f"; on the nearest hole its optic misses the crossing by {self.off_by:.3f} in"
        if self.combined and self.into_mount:
            text += "\n(the unused output port ends on the splitter's mount: see Checks)"
        return text


# --------------------------------------------------------------------- geometry
def _cross(a, b) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def _straight_on(a: BeamSegment, b: BeamSegment) -> bool:
    """Whether ``b`` continues ``a`` without turning (through a window, an on-axis lens, ...)."""
    return float(np.dot(a.direction, b.direction)) > SAME_DIRECTION


def _locate(trace: TraceResult, seg: BeamSegment, s: float) -> tuple[BeamSegment, float] | None:
    """The segment on ``seg``'s straight run that contains the point ``s`` along
    ``seg`` (negative: behind its start), and the distance into that segment."""
    while s < -POINT_TOLERANCE:
        if seg.parent is None:
            return None
        parent = trace.segments[seg.parent]
        if not _straight_on(parent, seg):
            return None
        s += parent.length
        seg = parent
    while s > seg.length + POINT_TOLERANCE:
        children = [c for c in trace.segments if c.parent == seg.index and _straight_on(seg, c)]
        if not children:
            return None
        s -= seg.length
        seg = max(children, key=lambda c: c.state.power)
    return seg, min(max(s, 0.0), seg.length)


def beam_crossing(trace: TraceResult, seg_t: BeamSegment, seg_r: BeamSegment) -> tuple[float, float]:
    """Where the paths of the two beams cross.  Raises :class:`CombineError`
    when they never do: parallel, crossing behind a source, or one beam is
    stopped or turned aside before it gets there."""
    d_t, d_r = np.asarray(seg_t.direction), np.asarray(seg_r.direction)
    det = _cross(d_t, d_r)
    if abs(det) < PARALLEL_LIMIT:
        raise CombineError("These beams are parallel: their paths never cross")
    w = np.asarray(seg_r.start) - np.asarray(seg_t.start)
    s, u = _cross(w, d_r) / det, _cross(w, d_t) / det
    point = np.asarray(seg_t.start) + s * d_t
    for name, seg, along in (("transmit", seg_t, s), ("reflect", seg_r, u)):
        if _locate(trace, seg, along) is None:
            where = "behind where it starts" if along < 0 else "after it is stopped or turned aside"
            raise CombineError(f"The beam to {name} would reach the crossing at "
                               f"({point[0]:.2f}, {point[1]:.2f}) in only {where}")
    return float(point[0]), float(point[1])


def splitter_normal(d_t, d_r) -> float:
    """Normal (degrees) of a surface that reflects direction ``d_r`` into ``d_t``,
    facing the incoming ``d_r`` beam."""
    n = np.asarray(d_t, float) - np.asarray(d_r, float)
    return math.degrees(math.atan2(n[1], n[0]))


def splitter_element(build: UnitBuild) -> OpticalElement | None:
    """The unit's beamsplitter, if it holds exactly one optic and that is a splitter."""
    if len(build.elements) == 1 and build.elements[0].kind is OpticKind.SPLITTER:
        return build.elements[0]
    return None


# ---------------------------------------------------------------------- planning
def _delivered(trace: TraceResult, d_t, d_r, owner: str) -> tuple[float, float]:
    """Power (mW) of T and of R that the splitter ``owner`` sends along ``d_t``;
    beams that run straight into its own mount do not count."""
    transmitted = reflected = 0.0
    for seg in trace.segments:
        if seg.parent is None:
            continue
        parent = trace.segments[seg.parent]
        if parent.end_owner != owner or parent.end_event != "split":
            continue
        if float(np.dot(seg.direction, d_t)) < SAME_DIRECTION:
            continue
        if seg.end_event == "blocked" and seg.end_owner == owner:
            continue
        incoming = np.asarray(parent.direction)
        if float(np.dot(incoming, d_t)) > SAME_DIRECTION:
            transmitted += seg.state.power
        elif float(np.dot(incoming, d_r)) > SAME_DIRECTION:
            reflected += seg.state.power
    return transmitted, reflected


def plan_combiner(
    comp: Component,
    seg_t: BeamSegment,
    seg_r: BeamSegment,
    trace: TraceResult,
    others: list[PlacedUnit],
    layout: Layout,
    cache: BuildCache | None = None,
) -> CombinePlan:
    """Place the splitter ``comp`` so that ``seg_r``'s beam reflects onto ``seg_t``'s.

    ``trace`` must be traced *without* ``comp`` (from ``others``); the segments
    come from it.  ``comp`` itself is not modified.
    """
    cache = cache or BuildCache()
    unit = get_unit_type(comp.type)
    point = beam_crossing(trace, seg_t, seg_r)
    d_t, d_r = np.asarray(seg_t.direction), np.asarray(seg_r.direction)
    normal = splitter_normal(d_t, d_r)
    board = layout.breadboard
    orientations = [name for name, _ in unit.orientations()] or [comp.orientation]
    current = comp.orientation or orientations[0]

    best: tuple[tuple, CombinePlan] | None = None
    for orientation in orientations:
        trial = replace(comp, orientation=orientation, params=comp.params)
        element = splitter_element(cache.build(trial))
        if element is None:
            raise CombineError(f"{unit.name} does not hold a beamsplitter")
        for flipped in (False, True):  # the reflected beam meets the front face, or the back
            angle = (normal + (180.0 if flipped else 0.0) - element.normal_angle) % 360.0
            ex, ey = Pose(0.0, 0.0, angle).apply(element.center)
            x, y = point[0] - ex, point[1] - ey
            off_by = 0.0
            if unit.mounting is Mounting.HOLE:
                x, y = board.nearest_hole(x, y)
                off_by = math.dist((x + ex, y + ey), point)
            candidate = replace(trial, x=float(x), y=float(y), angle=angle)
            result = Analyzer.trace([*others, cache.place(candidate)], layout)
            transmitted, reflected = _delivered(result, d_t, d_r, comp.id)
            # The splitter's other output port usually ends on its mount; fewer such hits is better.
            into_mount = sum(w.kind is WarningKind.OWN_MOUNT and w.owner == comp.id for w in result.warnings)
            plan = CombinePlan(
                x=float(x),
                y=float(y),
                angle=angle,
                orientation=orientation,
                point=point,
                direction=(float(d_t[0]), float(d_t[1])),
                transmitted=transmitted,
                reflected=reflected,
                off_by=off_by,
                into_mount=into_mount,
            )
            score = (
                (transmitted > 0) + (reflected > 0),
                round(transmitted + reflected, 9),
                -into_mount,
                orientation == current,
                not flipped,
            )
            if best is None or score > best[0]:
                best = (score, plan)
    assert best is not None
    return best[1]
