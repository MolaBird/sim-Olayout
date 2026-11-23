"""Simple solid primitives used to model optomechanical parts in 3D.

Parts are modelled as a handful of axis-aligned solids.  That is enough to

* render a convincing shaded top-down sprite (see :mod:`olayout.render`),
* derive the top-view footprint used for collision checks, and
* slice the part at beam height to know what blocks a beam.

Frames: x forward, y left, z up (right-handed).  Lengths in inches.
Rotations are restricted to multiples of 90 degrees so everything stays
axis-aligned (mounts are re-oriented by choosing a different mounting tap).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal, Union

import numpy as np
import shapely
from shapely.geometry import Polygon, box

from olayout.core.geometry import as_polygonal

Axis = Literal["x", "y", "z"]
Vec3 = tuple[float, float, float]
_AXES: tuple[Axis, ...] = ("x", "y", "z")
_CIRCLE_SEGMENTS = 12  # per quarter circle in footprints


@dataclass(frozen=True)
class Box:
    lo: Vec3
    hi: Vec3
    material: str = "anodized"

    def __post_init__(self):
        if any(h < l for l, h in zip(self.lo, self.hi)):
            raise ValueError(f"Box corners out of order: {self.lo} {self.hi}")


@dataclass(frozen=True)
class Cylinder:
    axis: Axis
    center: Vec3  # centre of the solid (mid-length on the axis)
    radius: float
    length: float
    material: str = "anodized"
    knurled: bool = False  # cosmetic: knurled knob texture


@dataclass(frozen=True)
class Prism:
    """A 2D outline (with optional holes) extruded vertically from z0 to z1."""

    outline: tuple[tuple[float, float], ...]
    z0: float
    z1: float
    material: str = "anodized"
    holes: tuple[tuple[tuple[float, float], ...], ...] = ()

    def polygon(self) -> Polygon:
        return Polygon(self.outline, self.holes)


@dataclass(frozen=True)
class Bore:
    """Material removed along an axis (an optic bore or clearance hole).

    Bores only affect the beam-height slice; they are not drawn.
    """

    axis: Axis
    center: Vec3
    radius: float
    length: float


Solid = Union[Box, Cylinder, Prism]
Primitive = Union[Box, Cylinder, Prism, Bore]


# --------------------------------------------------------------------------
# Right-angle rotations
# --------------------------------------------------------------------------
def rotation_matrix(axis: Axis, degrees: int) -> np.ndarray:
    """Integer rotation matrix for a multiple of 90 degrees about an axis."""
    if degrees % 90:
        raise ValueError("only multiples of 90 degrees are supported")
    c = round(math.cos(math.radians(degrees)))
    s = round(math.sin(math.radians(degrees)))
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def rotation_aligning(vector: Vec3, target: Vec3) -> np.ndarray:
    """A right-angle rotation taking axis-aligned unit ``vector`` onto ``target``."""
    v = np.asarray(vector, dtype=float)
    t = np.asarray(target, dtype=float)
    for axis in _AXES:
        for deg in (0, 90, 180, 270):
            rot = rotation_matrix(axis, deg)
            if np.allclose(rot @ v, t):
                return rot
    raise ValueError(f"no right-angle rotation maps {vector} to {target}")


def _rotated_axis(rot: np.ndarray, axis: Axis) -> Axis:
    unit = np.zeros(3)
    unit[_AXES.index(axis)] = 1.0
    mapped = rot @ unit
    return _AXES[int(np.argmax(np.abs(mapped)))]


def transform(prim: Primitive, rot: np.ndarray, offset: Vec3 = (0.0, 0.0, 0.0)) -> Primitive:
    """Rotate a primitive about the origin by ``rot`` then translate by ``offset``."""
    off = np.asarray(offset, dtype=float)
    if isinstance(prim, Box):
        corners = np.array([prim.lo, prim.hi]) @ rot.T + off
        return replace(prim, lo=tuple(corners.min(axis=0)), hi=tuple(corners.max(axis=0)))
    if isinstance(prim, (Cylinder, Bore)):
        center = tuple(rot @ np.asarray(prim.center) + off)
        return replace(prim, center=center, axis=_rotated_axis(rot, prim.axis))
    if isinstance(prim, Prism):
        if np.allclose(rot[2], [0, 0, 1]):
            z0, z1 = prim.z0, prim.z1
        elif np.allclose(rot[2], [0, 0, -1]):  # turned upside down: the outline is mirrored
            z0, z1 = -prim.z1, -prim.z0
        else:
            raise ValueError("prisms can only be turned about the vertical axis or upside down")
        rot2 = rot[:2, :2]
        move = lambda ring: tuple(tuple(map(float, rot2 @ np.asarray(p) + off[:2])) for p in ring)
        return replace(
            prim,
            outline=move(prim.outline),
            holes=tuple(move(h) for h in prim.holes),
            z0=z0 + off[2],
            z1=z1 + off[2],
        )
    raise TypeError(f"unknown primitive {prim!r}")


def transform_all(prims, rot: np.ndarray, offset: Vec3 = (0.0, 0.0, 0.0)) -> tuple[Primitive, ...]:
    return tuple(transform(p, rot, offset) for p in prims)


# --------------------------------------------------------------------------
# Extents, footprints and slices
# --------------------------------------------------------------------------
def bounds3d(prim: Primitive) -> tuple[np.ndarray, np.ndarray]:
    """Axis-aligned 3D bounding box (lo, hi)."""
    if isinstance(prim, Box):
        return np.asarray(prim.lo, float), np.asarray(prim.hi, float)
    if isinstance(prim, (Cylinder, Bore)):
        half = np.full(3, prim.radius)
        half[_AXES.index(prim.axis)] = prim.length / 2.0
        c = np.asarray(prim.center, float)
        return c - half, c + half
    if isinstance(prim, Prism):
        pts = np.asarray(prim.outline, float)
        lo = np.array([pts[:, 0].min(), pts[:, 1].min(), prim.z0])
        hi = np.array([pts[:, 0].max(), pts[:, 1].max(), prim.z1])
        return lo, hi
    raise TypeError(f"unknown primitive {prim!r}")


def _footprint(prim: Primitive):
    if isinstance(prim, Prism):
        return prim.polygon()
    if isinstance(prim, (Cylinder, Bore)) and prim.axis == "z":
        return shapely.Point(prim.center[0], prim.center[1]).buffer(prim.radius, quad_segs=_CIRCLE_SEGMENTS)
    lo, hi = bounds3d(prim)
    return box(lo[0], lo[1], hi[0], hi[1])


def footprint(prims) -> shapely.Geometry:
    """Top-view silhouette (union of all solid primitives)."""
    shapes = [_footprint(p) for p in prims if not isinstance(p, Bore)]
    return as_polygonal(shapely.union_all(shapes)) if shapes else Polygon()


def _slice(prim: Primitive, z: float):
    lo, hi = bounds3d(prim)
    if not lo[2] - 1e-9 <= z <= hi[2] + 1e-9:
        return None
    if isinstance(prim, Prism):
        return prim.polygon()
    if isinstance(prim, (Cylinder, Bore)):
        if prim.axis == "z":
            return _footprint(prim)
        dz = z - prim.center[2]
        half = math.sqrt(max(prim.radius**2 - dz * dz, 0.0))
        if half < 1e-6:
            return None
        cx, cy = prim.center[0], prim.center[1]
        half_len = prim.length / 2.0
        if prim.axis == "x":
            return box(cx - half_len, cy - half, cx + half_len, cy + half)
        return box(cx - half, cy - half_len, cx + half, cy + half_len)
    return box(lo[0], lo[1], hi[0], hi[1])


def slice_at(prims, z: float) -> shapely.Geometry:
    """Horizontal cross-section at height ``z`` with bores removed."""
    solids = [s for p in prims if not isinstance(p, Bore) and (s := _slice(p, z)) is not None]
    if not solids:
        return Polygon()
    section = shapely.union_all(solids)
    bores = [s for p in prims if isinstance(p, Bore) and (s := _slice(p, z)) is not None]
    if bores:
        section = section.difference(shapely.union_all(bores))
    return as_polygonal(section)


def height_range(prims) -> tuple[float, float]:
    solids = [bounds3d(p) for p in prims if not isinstance(p, Bore)]
    if not solids:
        return 0.0, 0.0
    return min(lo[2] for lo, _ in solids), max(hi[2] for _, hi in solids)
