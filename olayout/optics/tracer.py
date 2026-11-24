"""2D Gaussian beam tracer.

The beam's chief ray is cast through the scene; at every optical surface the
complex beam parameters are transformed (ABCD) and the ray is reflected,
refracted (thin lens), split, attenuated or absorbed.

Two q parameters are tracked: tangential (in the table plane, the one drawn
on the canvas) and sagittal (perpendicular to the table).  They differ only
after off-axis curved mirrors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum

import numpy as np

from olayout.core.geometry import normalize, polygon_edges, ray_segment_hits, reflect, unit_vector
from olayout.core.layout import IssueKey
from olayout.optics.elements import Blocker, Emitter, OpticalElement, OpticKind
from olayout.optics.gaussian import GaussianBeam, aperture_transmission, mirror_focal_lengths

T_MIN = 1e-7  # ignore hits closer than this (inches) to avoid re-hitting a surface
CLIP_WARN = 0.995  # flag clipping when less than this fraction passes an aperture
MIN_COS_INCIDENCE = 0.02  # grazing incidence: cap the footprint at 50x the beam


@dataclass(frozen=True)
class BeamState:
    q_t: complex  # tangential (in-plane)
    q_s: complex  # sagittal (out-of-plane)
    wavelength: float
    m2: float
    power: float

    @classmethod
    def from_emitter(cls, emitter: Emitter) -> BeamState:
        beam = GaussianBeam.from_waist(emitter.waist_radius, emitter.wavelength, -emitter.waist_distance, emitter.m2)
        return cls(beam.q, beam.q, emitter.wavelength, emitter.m2, emitter.power)

    @property
    def tangential(self) -> GaussianBeam:
        return GaussianBeam(self.q_t, self.wavelength, self.m2)

    @property
    def sagittal(self) -> GaussianBeam:
        return GaussianBeam(self.q_s, self.wavelength, self.m2)

    def propagate(self, distance: float) -> BeamState:
        return replace(self, q_t=self.q_t + distance, q_s=self.q_s + distance)

    def lens(self, f_t: float, f_s: float) -> BeamState:
        return replace(self, q_t=self.tangential.thin_lens(f_t).q, q_s=self.sagittal.thin_lens(f_s).q)

    def scaled(self, factor: float) -> BeamState:
        return replace(self, power=self.power * factor)


@dataclass(frozen=True)
class BeamSegment:
    index: int
    parent: int | None
    source: str  # owner id of the emitter
    start: tuple[float, float]
    direction: tuple[float, float]
    length: float
    state: BeamState  # beam at the start of the segment
    path_start: float  # optical path from the emitter to the segment start
    depth: int
    end_event: str  # reflect | transmit | split | blocked | escaped | limit
    end_owner: str = ""  # component hit at the end
    clip: float = 1.0  # fraction transmitted through the aperture at the end

    @property
    def end(self) -> tuple[float, float]:
        return self.point_at(self.length)

    def point_at(self, s: float) -> tuple[float, float]:
        return (self.start[0] + s * self.direction[0], self.start[1] + s * self.direction[1])

    def state_at(self, s: float) -> BeamState:
        return self.state.propagate(s)

    def waist_positions(self) -> list[float]:
        """Distances along the segment where the tangential beam has a waist."""
        s = -self.state.q_t.real
        return [s] if 0.0 <= s <= self.length else []


class WarningKind(str, Enum):
    CLIPPED = "clipped"  # an aperture cuts off part of the beam
    OWN_MOUNT = "own-mount"  # a beam leaving an optic runs into that optic's own mount
    SEGMENT_LIMIT = "segment-limit"  # tracing stopped early (e.g. light trapped in a cavity)


@dataclass(frozen=True)
class TraceWarning:
    kind: WarningKind
    owner: str  # component id
    message: str
    point: tuple[float, float]

    @property
    def key(self) -> IssueKey:
        return IssueKey.of(self.kind.value, [self.owner] if self.owner else [])


@dataclass
class TraceResult:
    segments: list[BeamSegment] = field(default_factory=list)
    warnings: list[TraceWarning] = field(default_factory=list)

    def nearest(self, point, max_distance: float) -> tuple[BeamSegment, float, float] | None:
        """Closest segment to ``point``: returns (segment, s along it, distance)."""
        if not self.segments:
            return None
        p = np.asarray(point, dtype=float)
        starts = np.array([seg.start for seg in self.segments])
        dirs = np.array([seg.direction for seg in self.segments])
        lengths = np.array([seg.length for seg in self.segments])
        s = np.clip(np.einsum("ij,ij->i", p - starts, dirs), 0.0, lengths)
        closest = starts + dirs * s[:, None]
        dist = np.hypot(*(p - closest).T)
        i = int(np.argmin(dist))
        if dist[i] > max_distance:
            return None
        return self.segments[i], float(s[i]), float(dist[i])

    def path_to(self, segment: BeamSegment) -> list[BeamSegment]:
        """The chain of segments from the emitter to ``segment`` (inclusive)."""
        chain = [segment]
        while chain[-1].parent is not None:
            chain.append(self.segments[chain[-1].parent])
        return chain[::-1]


def deflect_thin(direction, normal, tangent, offset: float, focal_length: float) -> np.ndarray:
    """Paraxial steering of a ray hitting a thin focusing surface off-centre."""
    if math.isinf(focal_length):
        return np.asarray(direction, dtype=float)
    axial = float(np.dot(direction, normal))
    if abs(axial) < 1e-12:
        return np.asarray(direction, dtype=float)
    sign = math.copysign(1.0, axial)
    slope = float(np.dot(direction, tangent)) / abs(axial) - offset / focal_length
    return normalize(sign * np.asarray(normal) + slope * np.asarray(tangent))


@dataclass
class _Ray:
    origin: np.ndarray
    direction: np.ndarray
    state: BeamState
    parent: int | None
    depth: int
    path: float
    source: str
    ignore: int = -1  # element index to skip (the one we just left)
    via: str = ""  # how the ray left that element: "transmitted" or "reflected"


class BeamTracer:
    def __init__(
        self,
        elements: list[OpticalElement],
        blockers: list[Blocker],
        bounds: tuple[float, float, float, float],
        max_segments: int = 1500,
        max_depth: int = 60,
        min_power_fraction: float = 1e-4,
        edges: list[tuple[np.ndarray, str]] | None = None,
    ):
        """``edges`` optionally supplies blocker boundaries pre-split into
        (N, 2, 2) segment arrays with their owner, instead of ``blockers``."""
        self.elements = [e for e in elements if e.kind is not OpticKind.NONE]
        self.bounds = bounds
        self.max_segments = max_segments
        self.max_depth = max_depth
        self.min_power_fraction = min_power_fraction

        if self.elements:
            ends = [e.endpoints for e in self.elements]
            self._e0 = np.array([a for a, _ in ends])
            self._e1 = np.array([b for _, b in ends])
        else:
            self._e0 = self._e1 = np.zeros((0, 2))

        if edges is None:
            edges = [(polygon_edges(b.shape), b.owner) for b in blockers]
        arrays, owners = [], []
        for segment_array, owner in edges:
            arrays.append(segment_array)
            owners += [owner] * len(segment_array)
        stacked = np.concatenate(arrays) if arrays else np.zeros((0, 2, 2))
        self._b0, self._b1 = stacked[:, 0, :], stacked[:, 1, :]
        self._blocker_owner = owners

    # ------------------------------------------------------------------ API
    def trace(self, emitters: list[Emitter]) -> TraceResult:
        result = TraceResult()
        for emitter in emitters:
            self._trace_emitter(emitter, result)
        return result

    # -------------------------------------------------------------- helpers
    def _exit_distance(self, origin: np.ndarray, direction: np.ndarray) -> float:
        x0, y0, x1, y1 = self.bounds
        t_far = math.inf
        for o, d, lo, hi in ((origin[0], direction[0], x0, x1), (origin[1], direction[1], y0, y1)):
            if abs(d) > 1e-12:
                t_far = min(t_far, max((lo - o) / d, (hi - o) / d))
        return max(t_far, 0.0) if math.isfinite(t_far) else 0.0

    def _trace_emitter(self, emitter: Emitter, result: TraceResult) -> None:
        stack = [
            _Ray(
                origin=np.asarray(emitter.position, dtype=float),
                direction=unit_vector(emitter.angle),
                state=BeamState.from_emitter(emitter),
                parent=None,
                depth=0,
                path=0.0,
                source=emitter.owner,
            )
        ]
        min_power = emitter.power * self.min_power_fraction
        while stack:
            ray = stack.pop()
            if len(result.segments) >= self.max_segments:
                result.warnings.append(TraceWarning(
                        WarningKind.SEGMENT_LIMIT, ray.source, "Beam segment limit reached", tuple(ray.origin)
                    ))
                return
            self._step(ray, result, stack, min_power)

    def _step(self, ray: _Ray, result: TraceResult, stack: list[_Ray], min_power: float) -> None:
        o, d = ray.origin, ray.direction
        t_elem, _ = ray_segment_hits(o, d, self._e0, self._e1, T_MIN)
        if 0 <= ray.ignore < len(t_elem):
            t_elem[ray.ignore] = math.inf
        t_block, _ = ray_segment_hits(o, d, self._b0, self._b1, T_MIN)
        t_exit = self._exit_distance(o, d)

        i_elem = int(np.argmin(t_elem)) if len(t_elem) else -1
        i_block = int(np.argmin(t_block)) if len(t_block) else -1
        best_elem = t_elem[i_elem] if i_elem >= 0 else math.inf
        best_block = t_block[i_block] if i_block >= 0 else math.inf

        index = len(result.segments)
        common = dict(
            index=index,
            parent=ray.parent,
            source=ray.source,
            start=(float(o[0]), float(o[1])),
            direction=(float(d[0]), float(d[1])),
            state=ray.state,
            path_start=ray.path,
            depth=ray.depth,
        )

        if best_block <= best_elem and best_block <= t_exit and math.isfinite(best_block):
            owner = self._blocker_owner[i_block]
            result.segments.append(BeamSegment(length=float(best_block), end_event="blocked", end_owner=owner, **common))
            self._warn_own_mount(ray, owner, o + best_block * d, result)
            return
        if not math.isfinite(best_elem) or best_elem > t_exit:
            result.segments.append(BeamSegment(length=float(t_exit), end_event="escaped", **common))
            return

        element = self.elements[i_elem]
        t = float(best_elem)
        hit = o + t * d
        state = ray.state.propagate(t)
        children, event, clip = self._interact(element, hit, d, state)
        if clip < CLIP_WARN:
            name = element.name or element.owner
            result.warnings.append(
                TraceWarning(
                    WarningKind.CLIPPED, element.owner, f"{name}: beam clipped, {100 * clip:.1f}% passes", (hit[0], hit[1])
                )
            )
        result.segments.append(
            BeamSegment(length=t, end_event=event, end_owner=element.owner, clip=clip, **common)
        )
        if ray.depth + 1 > self.max_depth:
            return
        for direction, child_state, via in children:
            if child_state.power < min_power:
                continue
            stack.append(
                _Ray(
                    origin=hit,
                    direction=direction,
                    state=child_state,
                    parent=index,
                    depth=ray.depth + 1,
                    path=ray.path + t,
                    source=ray.source,
                    ignore=i_elem,
                    via=via,
                )
            )

    def _warn_own_mount(self, ray: _Ray, owner: str, point, result: TraceResult) -> None:
        """Explain a beam that is stopped by the mount of the optic it just left."""
        if not owner or not 0 <= ray.ignore < len(self.elements):
            return
        element = self.elements[ray.ignore]
        if element.owner != owner:
            return
        name = element.name or owner
        if ray.via == "transmitted":
            message = f"{name}: the transmitted beam hits its own mount; try the other mounting orientation (F)"
        else:
            message = f"{name}: the reflected beam hits its own mount"
        result.warnings.append(TraceWarning(WarningKind.OWN_MOUNT, owner, message, (float(point[0]), float(point[1]))))

    def _interact(self, element: OpticalElement, hit, d, state: BeamState):
        """Return (children [(direction, state, "transmitted"|"reflected")], event, clip fraction)."""
        n = element.normal
        tan = element.tangent
        offset = float(np.dot(hit - np.asarray(element.center), tan))
        cos_i = abs(float(np.dot(d, n)))
        incidence = math.degrees(math.acos(min(cos_i, 1.0)))
        # A beam meeting a surface at incidence i lights a strip 1/cos(i) wider than itself.
        footprint = state.tangential.radius / max(cos_i, MIN_COS_INCIDENCE)
        clip = aperture_transmission(footprint, element.half_aperture, offset - element.aperture_offset)
        state = state.scaled(clip)
        kind = element.kind

        if kind is OpticKind.BLOCK:
            return [], "blocked", 1.0
        if kind is OpticKind.MIRROR:
            if float(np.dot(d, n)) > 0:  # arriving from behind: opaque back side
                return [], "blocked", 1.0
            return [(*self._reflected(element, d, offset, incidence, state), "reflected")], "reflect", clip
        if kind is OpticKind.LENS:
            f = element.focal_length
            new_d = deflect_thin(d, n, tan, offset, f)
            return [(new_d, state.lens(f, f), "transmitted")], "transmit", clip
        if kind is OpticKind.WINDOW:
            return [(np.asarray(d), state.scaled(element.transmittance), "transmitted")], "transmit", clip
        if kind is OpticKind.SPLITTER:
            r = element.reflectance
            t = element.transmittance if element.transmittance < 1.0 else 1.0 - r
            children = []
            if t > 0:
                children.append((np.asarray(d), state.scaled(t), "transmitted"))
            if r > 0:
                ref_d, ref_state = self._reflected(element, d, offset, incidence, state)
                children.append((ref_d, ref_state.scaled(r), "reflected"))
            return children, "split", clip
        return [], "blocked", 1.0

    @staticmethod
    def _reflected(element: OpticalElement, d, offset: float, incidence: float, state: BeamState):
        n = element.normal
        new_d = reflect(d, n)
        f_t, f_s = mirror_focal_lengths(element.roc, incidence)
        if math.isfinite(element.roc):
            new_d = deflect_thin(new_d, n, element.tangent, offset, element.roc / 2.0)
        return new_d, state.lens(f_t, f_s)
