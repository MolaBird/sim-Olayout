"""2D geometry helpers: poses, vectors, ray casting, shapely transforms.

Conventions: lengths in inches, angles in degrees counter-clockwise,
x to the right and y "up" (away from the user) on the table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class Pose:
    """A rigid 2D transform: rotate by ``angle`` about the origin, then translate."""

    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0  # degrees, counter-clockwise

    @property
    def position(self) -> np.ndarray:
        return np.array([self.x, self.y])

    def _cos_sin(self) -> tuple[float, float]:
        rad = math.radians(self.angle)
        return math.cos(rad), math.sin(rad)

    def apply(self, points) -> np.ndarray:
        """Map local points, shape (N, 2) or (2,), into the parent frame."""
        pts = np.asarray(points, dtype=float)
        c, s = self._cos_sin()
        rot = np.array([[c, -s], [s, c]])
        return pts @ rot.T + np.array([self.x, self.y])

    def apply_vector(self, vectors) -> np.ndarray:
        """Rotate local direction vectors (no translation)."""
        vec = np.asarray(vectors, dtype=float)
        c, s = self._cos_sin()
        rot = np.array([[c, -s], [s, c]])
        return vec @ rot.T

    def inverse(self) -> Pose:
        c, s = self._cos_sin()
        # R^T * (-t)
        ix = -(c * self.x + s * self.y)
        iy = -(-s * self.x + c * self.y)
        return Pose(ix, iy, -self.angle)

    def compose(self, local: Pose) -> Pose:
        """Express a pose given in this pose's frame in the parent frame."""
        px, py = self.apply([local.x, local.y])
        return Pose(float(px), float(py), self.angle + local.angle)

    def shapely_matrix(self) -> list[float]:
        """Coefficients for :func:`shapely.affinity.affine_transform`."""
        c, s = self._cos_sin()
        return [c, -s, s, c, self.x, self.y]


def unit_vector(angle_deg: float) -> np.ndarray:
    rad = math.radians(angle_deg)
    return np.array([math.cos(rad), math.sin(rad)])


def angle_of(vector) -> float:
    """Direction angle of a vector in degrees, in (-180, 180]."""
    return math.degrees(math.atan2(vector[1], vector[0]))


def normalize(vector) -> np.ndarray:
    vec = np.asarray(vector, dtype=float)
    norm = float(np.hypot(vec[0], vec[1]))
    if norm == 0.0:
        raise ValueError("cannot normalize a zero-length vector")
    return vec / norm


def reflect(direction, normal) -> np.ndarray:
    """Mirror-reflect a direction about a surface with the given unit normal."""
    d = np.asarray(direction, dtype=float)
    n = np.asarray(normal, dtype=float)
    return d - 2.0 * float(np.dot(d, n)) * n


def cross2(a, b) -> np.ndarray:
    """z-component of the cross product of 2D vectors (broadcasts over rows)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def ray_segment_hits(origin, direction, p0: np.ndarray, p1: np.ndarray, t_min: float = 1e-9):
    """Intersect one ray with many segments at once.

    Returns ``(t, u)`` arrays: ``t`` is the distance along the (unit) ray to
    each segment (``inf`` when there is no hit) and ``u`` in [0, 1] is the
    position of the hit along the segment from ``p0`` to ``p1``.
    """
    o = np.asarray(origin, dtype=float)
    d = np.asarray(direction, dtype=float)
    p0 = np.asarray(p0, dtype=float).reshape(-1, 2)
    p1 = np.asarray(p1, dtype=float).reshape(-1, 2)
    e = p1 - p0
    w = p0 - o
    denom = cross2(d, e)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = cross2(w, e) / denom
        u = cross2(w, d) / denom
    valid = (np.abs(denom) > 1e-14) & (t > t_min) & (u >= -1e-12) & (u <= 1.0 + 1e-12)
    t = np.where(valid, t, np.inf)
    return t, np.clip(u, 0.0, 1.0)


def point_segment_distance(point, p0, p1) -> tuple[float, float]:
    """Distance from a point to segment p0-p1 and the clamped parameter along it."""
    p = np.asarray(point, dtype=float)
    a = np.asarray(p0, dtype=float)
    b = np.asarray(p1, dtype=float)
    ab = b - a
    length_sq = float(np.dot(ab, ab))
    if length_sq == 0.0:
        return float(np.hypot(*(p - a))), 0.0
    u = float(np.clip(np.dot(p - a, ab) / length_sq, 0.0, 1.0))
    closest = a + u * ab
    return float(np.hypot(*(p - closest))), u


def transform_geometry(geometry: BaseGeometry, pose: Pose) -> BaseGeometry:
    """Apply a pose to a shapely geometry (vectorized; hot path while dragging)."""
    if geometry.is_empty:
        return geometry
    c, s = pose._cos_sin()
    rot_t = np.array([[c, s], [-s, c]])  # transpose of the rotation matrix
    offset = np.array([pose.x, pose.y])
    return shapely.transform(geometry, lambda coords: coords @ rot_t + offset)


def polygon_edges(geometry: BaseGeometry) -> np.ndarray:
    """All boundary edges of a (multi)polygon as an (N, 2, 2) array."""
    edges: list[np.ndarray] = []
    for poly in getattr(geometry, "geoms", [geometry]):
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        for ring in [poly.exterior, *poly.interiors]:
            coords = np.asarray(ring.coords)
            if len(coords) >= 2:
                edges.append(np.stack([coords[:-1], coords[1:]], axis=1))
    if not edges:
        return np.zeros((0, 2, 2))
    return np.concatenate(edges, axis=0)


def as_polygonal(geometry: BaseGeometry) -> BaseGeometry:
    """Drop lines/points that boolean operations can leave behind."""
    if geometry.is_empty:
        return geometry
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    parts = [g for g in getattr(geometry, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts) if parts else shapely.Polygon()
