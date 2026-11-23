"""Inspect a vendor CAD file to register it in ``assets/cad/cad_parts.json``.

    python -m olayout.tools.cad_inspect assets/cad/KM100-E02-Step.step [--section y=-8]

Writes ``<file>.views.png`` showing the part seen down each CAD axis (+z, -z,
+x, -x, +y, -y) and prints the bounding box.  Each ``--section axis=value``
prints the closed loops of a cross-section at that CAD coordinate (mm):
mounting holes appear as small loops whose centroid is the tap axis.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString

from olayout.render import cad
from olayout.render.mesh import allocate_for_mesh, rasterize_mesh
from olayout.render.shading import shade

VIEWS = {  # rotation taking the named CAD axis to +z (i.e. looking down it)
    "+z": np.eye(3),
    "-z": np.diag([1.0, -1.0, -1.0]),
    "+x": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], dtype=float),
    "-x": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], dtype=float),
    "+y": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float),
    "-y": np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float),
}
AXES = "xyz"


def section_loops(vertices_mm: np.ndarray, faces: np.ndarray, axis: int, value: float):
    """Closed cross-section loops at ``vertices[:, axis] == value``."""
    tri = vertices_mm[faces]
    d = tri[..., axis] - value
    other = [a for a in range(3) if a != axis]
    segments = []
    for t, dd in zip(tri, d):
        pts = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            if (dd[i] < 0) != (dd[j] < 0):
                s = dd[i] / (dd[i] - dd[j])
                pts.append(tuple(np.round((t[i] + s * (t[j] - t[i]))[other], 5)))
        if len(pts) == 2 and pts[0] != pts[1]:
            segments.append(LineString(pts))
    if not segments:
        return []
    merged = shapely.line_merge(shapely.union_all(segments))
    return sorted(shapely.get_parts(shapely.polygonize(shapely.get_parts(merged))), key=lambda p: -p.area)


def render_views(vertices_in: np.ndarray, faces: np.ndarray, colors: np.ndarray, out: Path, ppi: float = 110) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    tiles = []
    for label, rot in VIEWS.items():
        v = vertices_in @ rot.T
        gbuffer = allocate_for_mesh(v, ppi * 2, 0.1)
        rasterize_mesh(gbuffer, v, faces, colors)
        rgba, _ = shade(gbuffer, 2)
        h, w, _ = rgba.shape
        tiles.append((label, QImage(np.ascontiguousarray(rgba).data, w, h, 4 * w, QImage.Format.Format_RGBA8888_Premultiplied).copy()))
    pad = 34
    width = sum(t.width() for _, t in tiles) + pad * (len(tiles) + 1)
    height = max(t.height() for _, t in tiles) + 2 * pad
    canvas = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor(118, 122, 128))
    painter = QPainter(canvas)
    painter.setFont(QFont("Arial", 11))
    x = pad
    for label, tile in tiles:
        painter.drawImage(x, pad, tile)
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(QRectF(x, 6, tile.width() + pad, 24), Qt.AlignmentFlag.AlignLeft, f"down {label}")
        x += tile.width() + pad
    painter.end()
    canvas.save(str(out))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--section", action="append", default=[], help="e.g. y=-8 (CAD axis = value in mm)")
    args = parser.parse_args(argv)
    has_trimesh, has_cascadio = cad.libraries_available()
    if not has_trimesh or (args.file.suffix.lower() in (".step", ".stp") and not has_cascadio):
        print("Install the optional CAD packages first:  pip install trimesh cascadio")
        return 1
    vertices_mm, faces, colors = cad._tessellate(args.file)  # noqa: SLF001 - developer tool
    lo, hi = vertices_mm.min(axis=0), vertices_mm.max(axis=0)
    print(f"{args.file.name}: {len(faces)} triangles")
    for k in range(3):
        print(f"  {AXES[k]}: {lo[k]:9.2f} .. {hi[k]:9.2f} mm   ({(hi[k] - lo[k]) / 25.4:.3f} in)")
    out = args.file.with_suffix(".views.png")
    render_views(vertices_mm / 25.4, faces, colors, out)
    print(f"views written to {out}  (each tile: right = +x', up = +y' after looking down the named axis)")
    for spec in args.section:
        axis_name, value = spec.split("=")
        loops = section_loops(vertices_mm, faces, AXES.index(axis_name.strip()), float(value))
        print(f"section {spec}: {len(loops)} closed loops")
        for loop in loops[:12]:
            c = loop.centroid
            print(f"   area {loop.area:8.2f} mm²  centroid ({c.x:8.2f}, {c.y:8.2f})  bounds {np.round(loop.bounds, 2).tolist()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
