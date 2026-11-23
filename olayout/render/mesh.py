"""Rasterize triangle meshes (tessellated CAD) into the top-down G-buffer.

Looking straight down, the visible surface at a pixel is the highest
triangle covering it.  Triangles are processed in batches grouped by their
pixel footprint so the inner loops stay in numpy even for 100k+ triangles.
"""

from __future__ import annotations

import math

import numpy as np

from olayout.render.materials import material_index
from olayout.render.raster import GBuffer

_SIZE_CLASSES = (1, 2, 3, 4, 6, 8, 12, 16)  # bbox-tested in batches; larger ones use scanlines
_MAX_CANDIDATES = 3_000_000  # samples per batch (bounds memory use)


def allocate_for_mesh(vertices: np.ndarray, ppi: float, margin: float) -> GBuffer:
    lo = vertices.min(axis=0)
    hi = vertices.max(axis=0)
    return GBuffer.allocate(lo[:2], hi[:2], ppi, margin)


def _samples_for(tri_px: np.ndarray, c0, r0, c1, r1, size: int, shape: tuple[int, int]):
    """Candidate pixel samples inside each triangle of a batch.

    Returns flat pixel indices, triangle indices (within the batch) and
    barycentric weights for every covered pixel centre.
    """
    rows, cols = shape
    grid = np.arange(size)
    dc, dr = np.meshgrid(grid, grid)
    dc, dr = dc.ravel(), dr.ravel()
    cc = c0[:, None] + dc[None, :]
    rr = r0[:, None] + dr[None, :]
    valid = (cc <= c1[:, None]) & (rr <= r1[:, None]) & (cc >= 0) & (rr >= 0) & (cc < cols) & (rr < rows)
    t_idx, k_idx = np.nonzero(valid)
    if t_idx.size == 0:
        return None
    px = cc[t_idx, k_idx].astype(np.float64)
    py = rr[t_idx, k_idx].astype(np.float64)
    a, b, c = tri_px[t_idx, 0], tri_px[t_idx, 1], tri_px[t_idx, 2]
    # Edge functions (signed areas) -> barycentric coordinates.
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    w0 = (b[:, 0] - px) * (c[:, 1] - py) - (b[:, 1] - py) * (c[:, 0] - px)
    w1 = (c[:, 0] - px) * (a[:, 1] - py) - (c[:, 1] - py) * (a[:, 0] - px)
    with np.errstate(divide="ignore", invalid="ignore"):
        l0, l1 = w0 / area, w1 / area
    l2 = 1.0 - l0 - l1
    eps = -1e-9
    inside = (l0 >= eps) & (l1 >= eps) & (l2 >= eps) & np.isfinite(l0)
    if not inside.any():
        return None
    pix = (rr[t_idx, k_idx] * cols + cc[t_idx, k_idx])[inside]
    return pix, t_idx[inside], np.stack([l0[inside], l1[inside], l2[inside]], axis=1)


def _scanline_samples(tri: np.ndarray, shape: tuple[int, int]):
    """Exactly the pixel centres covered by one (large) triangle, row by row."""
    rows, cols = shape
    r_lo = max(math.ceil(tri[:, 1].min()), 0)
    r_hi = min(math.floor(tri[:, 1].max()), rows - 1)
    if r_hi < r_lo:
        return None
    ys = np.arange(r_lo, r_hi + 1, dtype=np.float64)
    x_lo = np.full(ys.shape, np.inf)
    x_hi = np.full(ys.shape, -np.inf)
    for i, j in ((0, 1), (1, 2), (2, 0)):
        (x0, y0), (x1, y1) = tri[i], tri[j]
        if y0 == y1:
            continue
        t = (ys - y0) / (y1 - y0)
        on_edge = (t >= -1e-9) & (t <= 1.0 + 1e-9)
        x = x0 + t * (x1 - x0)
        x_lo = np.where(on_edge, np.minimum(x_lo, x), x_lo)
        x_hi = np.where(on_edge, np.maximum(x_hi, x), x_hi)
    finite = np.isfinite(x_lo) & np.isfinite(x_hi)
    c_lo = np.where(finite, np.maximum(np.ceil(x_lo - 1e-9), 0), 1).astype(np.int64)
    c_hi = np.where(finite, np.minimum(np.floor(x_hi + 1e-9), cols - 1), 0).astype(np.int64)
    counts = np.clip(c_hi - c_lo + 1, 0, None)
    total = int(counts.sum())
    if total == 0:
        return None
    row_idx = np.repeat(ys.astype(np.int64), counts)
    col_idx = np.repeat(c_lo, counts) + (np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts))
    px, py = col_idx.astype(np.float64), row_idx.astype(np.float64)
    a, b, c = tri
    area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if area == 0:
        return None
    l0 = ((b[0] - px) * (c[1] - py) - (b[1] - py) * (c[0] - px)) / area
    l1 = ((c[0] - px) * (a[1] - py) - (c[1] - py) * (a[0] - px)) / area
    bary = np.stack([l0, l1, 1.0 - l0 - l1], axis=1).clip(0.0, 1.0)
    return row_idx * cols + col_idx, bary


def rasterize_mesh(
    gbuffer: GBuffer,
    vertices: np.ndarray,
    faces: np.ndarray,
    face_colors: np.ndarray | None = None,
    material: str = "anodized",
) -> None:
    """Draw a triangle mesh (part-frame inches, z up) into the G-buffer.

    ``face_colors`` (M, 3) in [0, 1] override the material albedo per face.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    tri = vertices[faces]  # (M, 3, 3)
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    keep = lengths > 0
    normals[keep] /= lengths[keep, None]
    # Vertical faces are invisible from above; orient the rest upward.
    keep &= np.abs(normals[:, 2]) > 1e-4
    normals[normals[:, 2] < 0] *= -1.0
    tri, normals = tri[keep], normals[keep]
    colors = None if face_colors is None else np.asarray(face_colors, dtype=np.float32)[keep]
    if len(tri) == 0:
        return

    ppi = gbuffer.ppi
    tri_px = np.empty((len(tri), 3, 2))
    tri_px[..., 0] = (tri[..., 0] - gbuffer.x0) * ppi - 0.5
    tri_px[..., 1] = (gbuffer.y1 - tri[..., 1]) * ppi - 0.5
    c0 = np.ceil(tri_px[..., 0].min(axis=1)).astype(np.int64)
    c1 = np.floor(tri_px[..., 0].max(axis=1)).astype(np.int64)
    r0 = np.ceil(tri_px[..., 1].min(axis=1)).astype(np.int64)
    r1 = np.floor(tri_px[..., 1].max(axis=1)).astype(np.int64)
    span = np.maximum(c1 - c0, r1 - r0) + 1
    covers = (c1 >= c0) & (r1 >= r0)

    shape = gbuffer.shape
    all_pix, all_z, all_tri = [], [], []
    # Large triangles: exact scanline coverage, one triangle at a time.
    for t in np.nonzero(covers & (span > _SIZE_CLASSES[-1]))[0]:
        found = _scanline_samples(tri_px[t], shape)
        if found is not None:
            pix, bary = found
            all_pix.append(pix)
            all_z.append(bary @ tri[t, :, 2])
            all_tri.append(np.full(len(pix), t, dtype=np.int64))
    # Small triangles: test every pixel of their bounding box, in batches.
    previous = 0
    for size in _SIZE_CLASSES:
        members = np.nonzero(covers & (span > previous) & (span <= size))[0]
        previous = max(previous, size)
        if members.size == 0:
            continue
        per_batch = max(1, _MAX_CANDIDATES // (size * size))
        for start in range(0, members.size, per_batch):
            batch = members[start : start + per_batch]
            found = _samples_for(tri_px[batch], c0[batch], r0[batch], c1[batch], r1[batch], size, shape)
            if found is None:
                continue
            pix, local, bary = found
            tri_ids = batch[local]
            z = np.einsum("ij,ij->i", bary, tri[tri_ids][..., 2])
            all_pix.append(pix)
            all_z.append(z)
            all_tri.append(tri_ids)
    if not all_pix:
        return
    pix = np.concatenate(all_pix)
    z = np.concatenate(all_z)
    tri_ids = np.concatenate(all_tri)

    # Depth resolve: highest surface wins, also against what is already drawn.
    height = gbuffer.height.ravel()
    depth = np.where(np.isnan(height), -np.inf, height)
    np.maximum.at(depth, pix, z)
    winners = z >= depth[pix] - 1e-12
    pix_w, tri_w, z_w = pix[winners], tri_ids[winners], z[winners]
    height[pix_w] = z_w
    normal = gbuffer.normal.reshape(-1, 3)
    normal[pix_w] = normals[tri_w]
    mat = gbuffer.material.ravel()
    mat[pix_w] = material_index(material)
    if colors is not None:
        gbuffer.ensure_color()
        gbuffer.color.reshape(-1, 3)[pix_w] = colors[tri_w]
    elif gbuffer.color is not None:
        gbuffer.color.reshape(-1, 3)[pix_w] = np.nan


def mesh_bounds_ok(vertices: np.ndarray) -> bool:
    extent = vertices.max(axis=0) - vertices.min(axis=0)
    return bool(np.all(np.isfinite(extent)) and extent[:2].max() < 48.0 and math.isfinite(float(extent.sum())))
