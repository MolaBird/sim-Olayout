"""Aim a beam: turn one component so a beam passes through a point.

Pick a component, a beam that comes from it, and a point.  The component
turns about its post axis until that beam passes exactly through the point.
The beam may leave the component directly (a mirror's reflection, a source's
output, a splitter's reflected port...) or after further *flat* optics that
stay put: mirrors, splitters (either port), windows and apertures.  Lenses,
curved mirrors and beam expanders in between are refused.

The optic usually sits off the post axis (a KM100's mirror is ~0.57" ahead
of it), so turning the mount also moves the spot where the beam meets it: a
mirror turned by half the wanted deflection misses.  The angle is therefore
solved numerically, against the real optics:

* the beam is identified by its *lineage*: its source, and at each optic it
  met, which component and which port (reflected / transmitted) it took;
* the flat optics after the turned component map its outgoing beam onto the
  final one by a fixed chain of reflections (an isometry), so the target is
  *unfolded*: reflected back through them, last first, to a virtual point
  the outgoing beam itself must pass.  However many mirrors lie in between,
  each trial angle costs the same;
* at a trial angle only the component itself is traced, starting from the
  fixed beam that arrives at it (or from its own emitter), which takes a
  fraction of a millisecond;
* Newton steps from the current angle find the nearest solution; if they
  fail, a scan over the full turn brackets every solution and the smallest
  turn wins;
* the real path is then followed through the flat optics, which must catch
  the beam on their glass (and a mirror on its front); a solution that
  slides off one is dropped for the next;
* one full trace of the layout confirms it and tells whether anything
  stops the beam before it gets there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from olayout.core.geometry import Pose, point_segment_distance, polygon_edges, ray_segment_hits, reflect
from olayout.core.layout import Component, Layout
from olayout.core.units import normalize_angle
from olayout.engine.analysis import TRACE_MARGIN, Analyzer
from olayout.engine.world import BuildCache, PlacedUnit
from olayout.optics.elements import OpticalElement, OpticKind
from olayout.optics.tracer import BeamSegment, BeamTracer, TraceResult

MISS_TOLERANCE = 1e-6  # inches: the beam passes the point this closely
NEWTON_STEPS = 40
MAX_NEWTON_TURN = 20.0  # degrees per Newton step
SCAN_STEP = 1.0  # degrees, when Newton fails
DERIVATIVE_STEP = 1e-3  # degrees
CONFIRM_TOLERANCE = 1e-5  # inches: the full trace must agree this well
FLAT_KINDS = (OpticKind.MIRROR, OpticKind.SPLITTER, OpticKind.WINDOW)  # may lie between


class AimError(ValueError):
    """The beam cannot be aimed there (the message is shown to the user)."""


# ------------------------------------------------------------- beam identity
@dataclass(frozen=True)
class BeamKey:
    """Which beam: its source, and (component, port) at each optic on the way."""

    source: str
    steps: tuple[tuple[str, str], ...]

    @property
    def component(self) -> str:
        """The component the beam leaves (its last optic, or its source)."""
        return self.steps[-1][0] if self.steps else self.source


def _port(child: BeamSegment, parent: BeamSegment) -> str:
    """How ``child`` left the optic at the end of ``parent``."""
    if parent.end_event == "reflect":
        return "reflected"
    if parent.end_event == "split":
        straight = float(np.dot(child.direction, parent.direction)) > 1.0 - 1e-9
        return "transmitted" if straight else "reflected"
    return "transmitted"


def beam_key(trace: TraceResult, segment: BeamSegment) -> BeamKey:
    chain = trace.path_to(segment)
    steps = tuple((a.end_owner, _port(b, a)) for a, b in zip(chain, chain[1:]))
    return BeamKey(segment.source, steps)


def leaving_component(trace: TraceResult, segment: BeamSegment) -> str:
    """The component a segment starts at: the optic it left, or its source."""
    return segment.source if segment.parent is None else trace.segments[segment.parent].end_owner


def find_beam(trace: TraceResult, key: BeamKey) -> BeamSegment | None:
    """The segment with this lineage in ``trace``, if the beam still exists."""
    children: dict[int | None, list[BeamSegment]] = {}
    for seg in trace.segments:
        children.setdefault(seg.parent, []).append(seg)
    for current in children.get(None, []):
        if current.source != key.source:
            continue
        for owner, port in key.steps:
            if current.end_owner != owner:
                current = None
                break
            current = next((c for c in children.get(current.index, []) if _port(c, current) == port), None)
            if current is None:
                break
        if current is not None:
            return current
    return None


# ----------------------------------------------------------------- the route
@dataclass(frozen=True)
class FlatSurface:
    """A fixed flat optic the beam meets after the turned component."""

    element: OpticalElement  # world frame
    port: str  # "reflected" or "transmitted"
    name: str  # its component's label

    def image(self, point: np.ndarray) -> np.ndarray:
        """Where ``point`` appears seen through this surface: its mirror image if it reflects."""
        if self.port != "reflected":
            return point
        n = self.element.normal
        return point - 2.0 * float(np.dot(point - np.asarray(self.element.center), n)) * n

    def pass_ray(self, origin: np.ndarray, direction: np.ndarray) -> tuple[np.ndarray, np.ndarray] | str:
        """The ray leaving this surface, or why the beam would not get through it."""
        t, _ = ray_segment_hits(origin, direction, *self.element.endpoints)
        if not math.isfinite(t[0]):
            return f"the beam would miss {self.name}"
        if self.element.kind is OpticKind.MIRROR and float(np.dot(direction, self.element.normal)) > 0:
            return f"the beam would reach the back of {self.name}"
        hit = origin + float(t[0]) * direction
        return hit, (reflect(direction, self.element.normal) if self.port == "reflected" else direction)


@dataclass(frozen=True)
class AimRoute:
    """How a picked beam comes from the component that turns."""

    component_id: str
    entry: BeamSegment  # the beam arriving at the component (unused when it is the source)
    own_source: bool  # the beam starts at the component's own emitter
    ports: tuple[str, ...]  # the ports it takes inside the component
    surfaces: tuple[FlatSurface, ...]  # the fixed flat optics after it, in order
    segments: tuple[BeamSegment, ...]  # from where it leaves the component to the picked segment

    @property
    def via(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s.name for s in self.surfaces))


def _element_at(elements: list[OpticalElement], point) -> OpticalElement | None:
    """The element a beam met at ``point``."""
    best = min(elements, key=lambda e: point_segment_distance(point, *e.endpoints)[0], default=None)
    return best if best is not None and point_segment_distance(point, *best.endpoints)[0] < 1e-6 else None


def aim_route(
    comp: Component, segment: BeamSegment, trace: TraceResult, others: list[PlacedUnit], layout: Layout
) -> AimRoute:
    """How ``segment`` comes from ``comp``; AimError says why it cannot be aimed by turning ``comp``."""
    name = comp.label or comp.id
    chain = trace.path_to(segment)
    key = beam_key(trace, segment)
    met = [s.end_owner for s in chain[:-1]]  # the component met at the end of each segment
    visits = [j for j, owner in enumerate(met) if owner == comp.id]
    if visits:
        last = first = visits[-1]
        while first > 0 and met[first - 1] == comp.id:
            first -= 1
        if visits[0] < first or (first > 0 and chain[0].source == comp.id):
            raise AimError(f"This beam passes {name} more than once")
        own_source, leave = first == 0 and chain[0].source == comp.id, last + 1
    elif chain[0].source == comp.id:
        first, own_source, leave = 0, True, 0
    else:
        raise AimError(f"This beam does not come from {name}")

    units = {u.id: u for u in others}
    surfaces = []
    for j in range(leave, len(chain) - 1):
        owner, port = key.steps[j]
        other = layout.component(owner)
        other_name = (other.label or other.id) if other is not None else owner
        unit = units.get(owner)
        element = _element_at(unit.world_optics().bundle.elements, chain[j].end) if unit is not None else None
        flat = element is not None and element.kind in FLAT_KINDS and math.isinf(element.roc)
        if not flat:
            raise AimError(
                f"The beam passes {other_name} on the way, which focuses it or is not a single flat surface: "
                f"only flat mirrors, splitters and windows may lie between {name} and the point"
            )
        surfaces.append(FlatSurface(element, port, other_name))
    return AimRoute(
        component_id=comp.id,
        entry=chain[first],
        own_source=own_source,
        ports=tuple(port for _, port in key.steps[first:leave]),
        surfaces=tuple(surfaces),
        segments=tuple(chain[leave:]),
    )


# ------------------------------------------------------------------ the plan
@dataclass(frozen=True)
class AimPlan:
    component_id: str
    angle: float  # the component's new angle
    turn: float  # signed change from its current angle, degrees
    path: tuple[tuple[float, float], ...]  # where the beam leaves the component, meets each optic on the way, the point
    direction: tuple[float, float]  # of the beam passing the point
    target: tuple[float, float]
    via: tuple[str, ...] = ()  # the optics between the component and the point
    stopped_by: str = ""  # what stops the beam before the point ("" = nothing)

    @property
    def start(self) -> tuple[float, float]:
        """Where the beam that passes the point starts (the component, or the last optic on the way)."""
        return self.path[-2]

    @property
    def distance(self) -> float:
        """Beam path from the component to the point."""
        return sum(math.dist(a, b) for a, b in zip(self.path, self.path[1:]))

    def summary(self, label: str) -> str:
        via = f" via {', '.join(self.via)}" if self.via else ""
        text = (
            f"{label or 'It'} turns {self.turn:+.2f}° to {self.angle:.2f}°: the beam passes "
            f"({self.target[0]:.3f}, {self.target[1]:.3f}) in{via}, {self.distance:.3f} in on"
        )
        if self.stopped_by:
            text += f"; ⚠ {self.stopped_by} is in the way"
        return text


# --------------------------------------------------------------- the solver
class _Turnable:
    """One component's optics, placed at any angle about its post (fast)."""

    def __init__(self, comp: Component, cache: BuildCache):
        self.comp = comp
        build = cache.build(comp)
        self.elements = build.elements
        self.emitters = build.emitters
        edges = [polygon_edges(b.shape) for b in build.blockers]
        self.edges = np.concatenate(edges) if edges else np.zeros((0, 2, 2))

    def tracer(self, angle: float, bounds, depth: int) -> tuple[BeamTracer, list]:
        pose = Pose(self.comp.x, self.comp.y, angle)
        rad = math.radians(angle)
        rotation = np.array([[math.cos(rad), -math.sin(rad)], [math.sin(rad), math.cos(rad)]])
        edges = self.edges @ rotation.T + np.array([self.comp.x, self.comp.y])
        tracer = BeamTracer(
            [e.transformed(pose, self.comp.id) for e in self.elements],
            [],
            bounds,
            max_depth=depth,
            edges=[(edges, self.comp.id)] if len(edges) else [],
        )
        return tracer, [e.transformed(pose, self.comp.id) for e in self.emitters]


class _Problem:
    """The beam leaving the component as a function of its angle, aimed at the
    target *unfolded* back through the flat optics after it."""

    def __init__(self, comp: Component, route: AimRoute, target: tuple[float, float], cache: BuildCache):
        self.route = route
        self.target = np.asarray(target, dtype=float)
        virtual = self.target
        for surface in reversed(route.surfaces):  # the last mirror's image first
            virtual = surface.image(virtual)
        self.virtual = virtual  # where the beam leaving the component must pass
        self.turnable = _Turnable(comp, cache)
        points = [self.target, self.virtual, np.array([comp.x, comp.y]), np.asarray(route.entry.start)]
        lo, hi = np.min(points, axis=0) - TRACE_MARGIN, np.max(points, axis=0) + TRACE_MARGIN
        self.bounds = (float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1]))

    def outgoing(self, angle: float) -> BeamSegment | None:
        """The beam leaving the component at this angle (None: it does not, e.g. it misses the optic)."""
        route = self.route
        tracer, emitters = self.turnable.tracer(angle, self.bounds, len(route.ports))
        if route.own_source:
            local = tracer.trace(emitters[:1])
        else:
            entry = route.entry
            local = tracer.follow(entry.start, entry.direction, entry.state, entry.source)
        if not local.segments:
            return None
        current = local.segments[0]
        for port in route.ports:
            current = next(
                (s for s in local.segments if s.parent == current.index and _port(s, current) == port), None
            )
            if current is None:
                return None
        return current

    def error(self, angle: float) -> float | None:
        """Signed angle (degrees) from the beam to the (unfolded) point, seen from where it leaves."""
        seg = self.outgoing(angle)
        if seg is None:
            return None
        v = self.virtual - np.asarray(seg.start)
        if np.hypot(*v) < 1e-6:
            raise AimError("The point is where the beam leaves the optic: pick a point further along")
        d = seg.direction
        return math.degrees(math.atan2(d[0] * v[1] - d[1] * v[0], d[0] * v[0] + d[1] * v[1]))

    def miss(self, angle: float) -> float:
        """How far the beam passes from the (unfolded) point, inches, or inf."""
        seg = self.outgoing(angle)
        if seg is None:
            return math.inf
        v = self.virtual - np.asarray(seg.start)
        d = np.asarray(seg.direction)
        along = float(np.dot(v, d))
        return abs(float(d[0] * v[1] - d[1] * v[0])) if along > 0 else math.inf

    def problem_at(self, angle: float) -> str | None:
        """Why the real beam does not get to the point at this angle (None: it does).

        The unfolded solution ignores the optics' edges; the real path must
        land on each one's glass, a mirror's front, with the point ahead."""
        seg = self.outgoing(angle)
        if seg is None:
            return "the beam would not leave it"
        origin, direction = np.asarray(seg.start, dtype=float), np.asarray(seg.direction, dtype=float)
        for surface in self.route.surfaces:
            passed = surface.pass_ray(origin, direction)
            if isinstance(passed, str):
                return passed
            origin, direction = passed
        if float(np.dot(self.target - origin, direction)) <= 0:
            last = self.route.surfaces[-1].name if self.route.surfaces else "it"
            return f"the point would be behind {last}"
        return None
def _newton(problem: _Problem, start: float) -> float | None:
    angle = start
    for _ in range(NEWTON_STEPS):
        err = problem.error(angle)
        if err is None:
            return None
        if problem.miss(angle) < MISS_TOLERANCE:
            return angle
        ahead = problem.error(angle + DERIVATIVE_STEP)
        if ahead is None:
            return None
        slope = (ahead - err) / DERIVATIVE_STEP
        if abs(slope) < 1e-6:
            return None
        step = max(-MAX_NEWTON_TURN, min(MAX_NEWTON_TURN, -err / slope))
        angle += step
    return angle if problem.miss(angle) < MISS_TOLERANCE else None


def _bisect(problem: _Problem, a: float, ea: float, b: float) -> float | None:
    for _ in range(60):
        mid = (a + b) / 2
        em = problem.error(mid)
        if em is None:
            return None
        if problem.miss(mid) < MISS_TOLERANCE:
            return mid
        if (em > 0) == (ea > 0):
            a, ea = mid, em
        else:
            b = mid
    return None


def _scan(problem: _Problem, start: float) -> tuple[list[float], bool]:
    """Every solution over a full turn, by bracketing sign changes of the error.

    Also tells whether a solution hides where the beam does not leave the
    component at all (it would miss the optic, or be stopped by the mount):
    the error, followed to the edge of an angle range where the beam gets
    out, heads for zero inside the gap beyond."""
    angles = start - 180.0 + SCAN_STEP * np.arange(int(360 / SCAN_STEP) + 1)
    samples = [(float(a), problem.error(float(a))) for a in angles]
    runs: list[list[tuple[float, float]]] = [[]]  # stretches of angles where the beam leaves
    for angle, err in samples:
        if err is None:
            if runs[-1]:
                runs.append([])
        else:
            runs[-1].append((angle, err))
    runs = [run for run in runs if run]

    roots, hidden = [], False
    for i, run in enumerate(runs):
        for (a0, e0), (a1, e1) in zip(run, run[1:]):
            if abs(e0) < 90 and abs(e1) < 90 and (e0 > 0) != (e1 > 0):  # the beam swept across the point
                root = _bisect(problem, a0, e0, a1)
                if root is not None:
                    roots.append(root)
        if len(run) < 2:
            continue
        edges = []  # (the two samples nearest the gap, edge sample last; the gap's far end)
        if run[0][0] > angles[0]:  # a gap before this run
            edges.append((run[1], run[0], runs[i - 1][-1][0] if i > 0 else float(angles[0])))
        if run[-1][0] < angles[-1]:  # a gap after it
            edges.append((run[-2], run[-1], runs[i + 1][0][0] if i + 1 < len(runs) else float(angles[-1])))
        for (a0, e0), (a1, e1), far in edges:
            if abs(e1) >= 90 or e0 == e1:
                continue
            zero = a1 - e1 * (a1 - a0) / (e1 - e0)  # where the error would reach zero
            if min(a1, far) < zero < max(a1, far):  # inside the gap
                hidden = True
    return roots, hidden


def turn_toward(
    comp: Component, segment: BeamSegment, target: tuple[float, float], trace: TraceResult, cache: BuildCache
) -> float | None:
    """The angle near ``comp``'s current one that sends the beam ``segment``
    (which leaves it directly) through ``target``, or None; quick enough to
    follow the mouse (no full trace)."""
    try:
        route = aim_route(comp, segment, trace, [], Layout())
        root = _newton(_Problem(comp, route, target, cache), comp.angle)
    except AimError:
        return None
    return None if root is None else normalize_angle(root)


def _turn(comp: Component, angle: float) -> float:
    """The signed turn from ``comp``'s angle to ``angle``, in [-180, 180)."""
    return normalize_angle(angle - comp.angle + 180.0) - 180.0


def plan_aim(
    comp: Component,
    segment: BeamSegment,
    target: tuple[float, float],
    trace: TraceResult,
    others: list[PlacedUnit],
    layout: Layout,
    cache: BuildCache,
) -> AimPlan:
    """Turn ``comp`` so the beam ``segment`` (which comes from it, directly or
    via flat optics that stay put) passes through ``target``."""
    name = comp.label or "it"
    route = aim_route(comp, segment, trace, others, layout)
    problem = _Problem(comp, route, target, cache)
    root = _newton(problem, comp.angle)
    if root is None or problem.problem_at(root) is not None:
        roots, hidden = _scan(problem, comp.angle)
        reasons = []
        root = None
        for candidate in sorted(roots, key=lambda r: abs(_turn(comp, r))):  # the smallest turn that works
            reason = problem.problem_at(candidate)
            if reason is None:
                root = candidate
                break
            reasons.append(reason)
        if root is None:
            if reasons:
                raise AimError(f"Turning {name} can point the beam there, but {reasons[0]}")
            if hidden:
                raise AimError(f"{name} would have to turn so far that the beam misses its optic or hits the mount")
            raise AimError(f"Turning {name} cannot send this beam through that point")
    turn = _turn(comp, root)
    turned = replace(comp, angle=normalize_angle(comp.angle + turn))

    # Confirm against the whole layout, and see whether anything is in the way.
    key = beam_key(trace, segment)
    full = Analyzer.trace([*others, cache.place(turned)], layout)
    seg = find_beam(full, key)
    v = np.asarray(target) - (np.asarray(seg.start) if seg is not None else 0.0)
    if seg is None or abs(float(seg.direction[0] * v[1] - seg.direction[1] * v[0])) > CONFIRM_TOLERANCE:
        raise AimError(f"Turned that way, the beam from {name} takes another path (something else is in the way)")
    along = float(np.dot(v, seg.direction))
    stopped_by = ""
    if seg.length < along - 1e-4:
        if seg.end_event == "escaped":
            stopped_by = "the edge of the traced area"
        else:
            owner = layout.component(seg.end_owner) if seg.end_owner else None
            stopped_by = (owner.label or owner.id) if owner is not None else "something"
            if seg.end_owner == comp.id:
                stopped_by = f"{stopped_by}'s own mount"
    chain = full.path_to(seg)
    path = [s.start for s in chain[len(chain) - len(route.segments):]] + [(float(target[0]), float(target[1]))]
    return AimPlan(
        component_id=comp.id,
        angle=turned.angle,
        turn=turn,
        path=tuple(path),
        direction=seg.direction,
        target=(float(target[0]), float(target[1])),
        via=route.via,
        stopped_by=stopped_by,
    )
