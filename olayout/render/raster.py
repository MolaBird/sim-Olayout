"""Rasterize solid primitives into a top-down G-buffer (height, normal, material).

Looking straight down, the visible surface at each pixel is the highest
solid there, so a z-buffer over primitives is all that is needed.  Curved
surfaces (horizontal cylinders such as adjuster knobs) get analytic normals;
edges and steps are shaded later from the height field.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely

from olayout.catalog.primitives import Box, Cylinder, Prism, Primitive, Bore, bounds3d
from olayout.render.materials import material_index


@dataclass
class GBuffer:
    x0: float  # part-frame x of the image's left edge
    y1: float  # part-frame y of the image's top edge
    ppi: float  # pixels per inch
    height: np.ndarray  # (H, W) float64, NaN where empty
    normal: np.ndarray  # (H, W, 3) float32
    material: np.ndarray  # (H, W) int16, -1 where empty
    color: np.ndarray | None = None  # (H, W, 3) albedo override (CAD colours), NaN = use material

    def ensure_color(self) -> None:
        if self.color is None:
            self.color = np.full(self.height.shape + (3,), np.nan, np.float32)

    @classmethod
    def allocate(cls, lo_xy, hi_xy, ppi: float, margin: float) -> GBuffer:
        x0 = lo_xy[0] - margin
        y1 = hi_xy[1] + margin
        width = max(1, math.ceil((hi_xy[0] + margin - x0) * ppi))
        height = max(1, math.ceil((y1 - (lo_xy[1] - margin)) * ppi))
        normal = np.zeros((height, width, 3), np.float32)
        normal[..., 2] = 1.0
        return cls(
            x0=x0,
            y1=y1,
            ppi=ppi,
            height=np.full((height, width), np.nan),
            normal=normal,
            material=np.full((height, width), -1, np.int16),
        )

    @property
    def shape(self) -> tuple[int, int]:
        return self.height.shape

    def window(self, x_lo: float, x_hi: float, y_lo: float, y_hi: float):
        """Pixel slices covering a part-frame rectangle, plus pixel-centre grids."""
        rows_total, cols_total = self.shape
        c0 = max(0, math.floor((x_lo - self.x0) * self.ppi))
        c1 = min(cols_total, math.ceil((x_hi - self.x0) * self.ppi) + 1)
        r0 = max(0, math.floor((self.y1 - y_hi) * self.ppi))
        r1 = min(rows_total, math.ceil((self.y1 - y_lo) * self.ppi) + 1)
        if c0 >= c1 or r0 >= r1:
            return None
        xs = self.x0 + (np.arange(c0, c1) + 0.5) / self.ppi
        ys = self.y1 - (np.arange(r0, r1) + 0.5) / self.ppi
        grid_x, grid_y = np.meshgrid(xs, ys)
        return slice(r0, r1), slice(c0, c1), grid_x, grid_y


def _horizontal_cylinder(across: np.ndarray, prim: Cylinder):
    """Height and normal components for a cylinder lying horizontally.

    ``across`` is the signed horizontal distance from the axis.
    """
    r = prim.radius
    sin_phi = np.clip(across / r, -1.0, 1.0)
    phi = np.arcsin(sin_phi)
    if prim.knurled:
        teeth = max(8, round(2 * math.pi * r / 0.035))
        phi = phi + 0.16 * np.sin(teeth * phi)
    z = prim.center[2] + r * np.cos(np.arcsin(sin_phi))
    return z, np.sin(phi), np.cos(phi)


def rasterize(gbuffer: GBuffer, prim: Primitive) -> None:
    """Draw one solid primitive into the G-buffer (keeping the highest surface)."""
    if isinstance(prim, Bore):
        return
    lo, hi = bounds3d(prim)
    win = gbuffer.window(lo[0], hi[0], lo[1], hi[1])
    if win is None:
        return
    rows, cols, gx, gy = win
    normal = np.zeros(gx.shape + (3,), np.float32)
    normal[..., 2] = 1.0

    if isinstance(prim, Box):
        mask = (gx >= lo[0]) & (gx <= hi[0]) & (gy >= lo[1]) & (gy <= hi[1])
        z = np.full(gx.shape, hi[2])
    elif isinstance(prim, Prism):
        mask = shapely.contains_xy(prim.polygon(), gx, gy)
        z = np.full(gx.shape, prim.z1)
    elif isinstance(prim, Cylinder):
        cx, cy, _ = prim.center
        if prim.axis == "z":
            mask = (gx - cx) ** 2 + (gy - cy) ** 2 <= prim.radius**2
            z = np.full(gx.shape, hi[2])
        elif prim.axis == "x":
            mask = (np.abs(gx - cx) <= prim.length / 2) & (np.abs(gy - cy) <= prim.radius)
            z, ny, nz = _horizontal_cylinder(gy - cy, prim)
            normal[..., 1], normal[..., 2] = ny, nz
        else:  # axis "y"
            mask = (np.abs(gy - cy) <= prim.length / 2) & (np.abs(gx - cx) <= prim.radius)
            z, nx, nz = _horizontal_cylinder(gx - cx, prim)
            normal[..., 0], normal[..., 2] = nx, nz
    else:
        raise TypeError(f"cannot rasterize {prim!r}")

    current = gbuffer.height[rows, cols]
    take = mask & ~(current >= z)  # NaN-safe "z is higher than what's there"
    if not take.any():
        return
    current[take] = z[take]
    gbuffer.normal[rows, cols][take] = normal[take]
    gbuffer.material[rows, cols][take] = material_index(prim.material)


def rasterize_all(prims, ppi: float, margin: float) -> GBuffer:
    solids = [p for p in prims if not isinstance(p, Bore)]
    if not solids:
        raise ValueError("nothing to render")
    los, his = zip(*(bounds3d(p) for p in solids))
    lo = np.min(los, axis=0)
    hi = np.max(his, axis=0)
    gbuffer = GBuffer.allocate(lo[:2], hi[:2], ppi, margin)
    for prim in solids:
        rasterize(gbuffer, prim)
    return gbuffer
