"""Triangle-mesh rasterizer and vendor-CAD registry."""

import json

import numpy as np
import pytest

from olayout import paths
from olayout.render import cad
from olayout.render.mesh import allocate_for_mesh, rasterize_mesh


def box_mesh(lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    vertices = np.array(
        [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]],
        dtype=float,
    )
    faces = np.array(
        [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
         [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    )
    return vertices, faces


@pytest.mark.parametrize("ppi", [8, 100])  # small-triangle batches and large-triangle scanlines
def test_mesh_rasterizer_covers_box_top(ppi):
    vertices, faces = box_mesh((0.0, 0.0, 0.0), (1.0, 2.0, 0.5))
    gbuffer = allocate_for_mesh(vertices, ppi, 0.1)
    rasterize_mesh(gbuffer, vertices, faces)
    covered = ~np.isnan(gbuffer.height)
    assert covered.sum() == pytest.approx(2.0 * ppi * ppi, rel=0.1)
    assert np.allclose(gbuffer.height[covered], 0.5)
    assert np.allclose(gbuffer.normal[covered][:, 2], 1.0)


def test_mesh_rasterizer_keeps_highest_surface():
    low_v, low_f = box_mesh((0, 0, 0), (2, 2, 0.2))
    high_v, high_f = box_mesh((0.5, 0.5, 0), (1.5, 1.5, 1.0))
    vertices = np.vstack([low_v, high_v])
    faces = np.vstack([low_f, high_f + len(low_v)])
    gbuffer = allocate_for_mesh(vertices, 50, 0.1)
    rasterize_mesh(gbuffer, vertices, faces)
    centre = gbuffer.window(0.99, 1.01, 0.99, 1.01)
    rows, cols, _, _ = centre
    assert np.allclose(gbuffer.height[rows, cols], 1.0)
    corner = gbuffer.window(0.1, 0.12, 0.1, 0.12)
    assert np.allclose(gbuffer.height[corner[0], corner[1]], 0.2)


def _write_ascii_stl(path, vertices, faces):
    lines = ["solid box"]
    for a, b, c in faces:
        lines += ["facet normal 0 0 0", " outer loop"]
        lines += [f"  vertex {x} {y} {z}" for x, y, z in vertices[[a, b, c]]]
        lines += [" endloop", "endfacet"]
    lines.append("endsolid box")
    path.write_text("\n".join(lines), encoding="ascii")


def test_cad_registry_aligns_mesh(tmp_path, monkeypatch):
    pytest.importorskip("trimesh")
    vertices_mm, faces = box_mesh((10.0, 20.0, 30.0), (35.4, 45.4, 55.4))  # a 1" cube
    _write_ascii_stl(tmp_path / "cube.stl", vertices_mm, faces)
    (tmp_path / cad.REGISTRY_FILE).write_text(
        json.dumps({"CUBE": {"file": "cube.stl", "axes": ["z", "x", "y"], "origin_mm": [10.0, 20.0, 30.0]},
                    "MISSING": {"file": "nope.step"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "CAD_DIR", tmp_path)
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    cad.load_registry.cache_clear()
    try:
        registry = cad.load_registry()
        assert set(registry) == {"CUBE", "MISSING"}  # kept: a committed sprite may exist
        assert registry["CUBE"].readable() and not registry["MISSING"].readable()
        verts, tri, colors = cad.part_mesh(registry["CUBE"])
        # part x = CAD z, part y = CAD x, part z = CAD y; origin moved to (10, 20, 30) mm.
        assert verts.min(axis=0) == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
        assert verts.max(axis=0) == pytest.approx([1.0, 1.0, 1.0], abs=1e-9)
        assert len(tri) == len(faces) and colors.shape == (len(faces), 3)
        assert list((tmp_path / "cache" / "cad").glob("CUBE-*.npz"))  # tessellated once, cached
    finally:
        cad.load_registry.cache_clear()


def test_lid_is_hidden_but_the_walls_stay():
    walls_v, walls_f = box_mesh((0, 0, 0), (2, 2, 1))
    lid_v, lid_f = box_mesh((0, 0, 1), (2, 2, 1.2))
    vertices = np.vstack([walls_v, lid_v])
    faces = np.vstack([walls_f, lid_f + len(walls_v)])
    hidden = cad.lid_faces(vertices, faces, top=1.0)
    assert hidden[len(walls_f):].all()  # the whole lid
    kept = faces[~hidden]
    assert len(kept) == 10  # the walls' four sides and top; their bottom faces down and is never seen
    assert vertices[kept][:, :, 2].max() == pytest.approx(1.0)


def test_bad_axes_rejected():
    with pytest.raises(ValueError):
        cad.CadSource("X", paths.CAD_DIR / "x.step", ("x", "x", "z"), (0, 0, 0)).rotation()
