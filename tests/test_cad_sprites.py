"""Committed CAD sprites (assets/sprites): used without the vendor files,
re-rendered when a local CAD file changes."""

import dataclasses

import numpy as np
import pytest

from olayout import paths
from olayout.catalog.units import catalog_parts
from olayout.render import cad, cad_sprites
from olayout.render.cad_sprites import CadSprites, read_png, write_png


def _record_renders(monkeypatch) -> list:
    """Replace the CAD renderer by a recorder (the store's fallbacks swallow exceptions)."""
    calls: list = []
    monkeypatch.setattr(cad_sprites, "render_cad_sprite", lambda *args: calls.append(args))
    return calls


def _write_box_stl(path, size_mm, name="box"):
    """A closed box (ASCII STL); ``name`` changes the file size between versions."""
    s = size_mm
    v = np.array([[0, 0, 0], [s, 0, 0], [s, s, 0], [0, s, 0], [0, 0, s], [s, 0, s], [s, s, s], [0, s, s]], float)
    faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
             [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    lines = [f"solid {name}"]
    for face in faces:
        lines += ["facet normal 0 0 0", " outer loop", *(f"  vertex {x} {y} {z}" for x, y, z in v[face])]
        lines += [" endloop", "endfacet"]
    lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines), encoding="ascii")


def test_png_round_trip_keeps_premultiplied_pixels(tmp_path):
    rgba = np.zeros((6, 9, 4), np.uint8)
    rgba[1:5, 2:7] = (40, 80, 120, 255)
    rgba[2, 3] = (20, 10, 5, 128)  # half-transparent, premultiplied
    write_png(tmp_path / "s.png", rgba)
    assert np.abs(read_png(tmp_path / "s.png").astype(int) - rgba).max() <= 1
    assert read_png(tmp_path / "missing.png") is None


def test_committed_sprites_cover_every_cad_part():
    """A clone without the vendor STEP files must still draw every CAD part."""
    store = CadSprites(paths.BAKED_SPRITES_DIR)
    missing = []
    for part in catalog_parts():
        key = store.key_for(part.part_id, part.roll, part.z_offset)
        if key is not None and (store.entry(key) is None or not (paths.BAKED_SPRITES_DIR / f"{key}.png").exists()):
            missing.append(part.sprite_name)
    assert not missing, f"not baked (run python -m olayout.tools.bake_sprites): {missing}"


def test_clone_without_cad_files_uses_committed_sprites(tmp_path, monkeypatch):
    # The same registry entries, but the STEP files are absent (as in a fresh clone).
    registry = {pid: dataclasses.replace(src, path=tmp_path / src.path.name) for pid, src in cad.load_registry().items()}
    renders = _record_renders(monkeypatch)
    store = CadSprites(paths.BAKED_SPRITES_DIR, registry)
    km100 = next(p for p in catalog_parts() if p.part_id == "KM100")
    sprite = store.get(km100.part_id, km100.roll, km100.z_offset)
    assert not renders
    assert sprite is not None and sprite.rgba[..., 3].any()
    assert sprite.shadow.shape == sprite.rgba.shape[:2] and sprite.shadow.max() > 0.5
    assert store.get("NOT-A-CAD-PART") is None  # drawn from its primitive model instead


def test_sprite_rebaked_when_cad_file_changes(tmp_path, monkeypatch):
    pytest.importorskip("trimesh")
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    stl = tmp_path / "cube.stl"
    _write_box_stl(stl, 25.4)
    registry = {"CUBE": cad.CadSource("CUBE", stl, ("x", "y", "z"), (0.0, 0.0, 0.0), material="steel")}
    baked_dir = tmp_path / "sprites"

    first = CadSprites(baked_dir, registry, ppi=40).get("CUBE")
    (png,) = baked_dir.glob("CUBE-*.png")
    entry = CadSprites(baked_dir, registry, ppi=40).entry(png.stem)
    assert entry.source_sha1 == cad.content_sha1(stl)
    assert first.width_in == pytest.approx(1.0 + 2 * 0.12, abs=0.05)

    # Unchanged file: the baked sprite is reused, nothing is rendered.
    with monkeypatch.context() as m:
        renders = _record_renders(m)
        again = CadSprites(baked_dir, registry, ppi=40).get("CUBE")
    assert not renders
    assert np.array_equal(again.rgba, first.rgba)

    # A new revision of the file: re-rendered in place, under the same key.
    _write_box_stl(stl, 50.8, name="bigger_box")
    bigger = CadSprites(baked_dir, registry, ppi=40).get("CUBE")
    assert bigger.width_in == pytest.approx(2.0 + 2 * 0.12, abs=0.05)
    assert [p.name for p in baked_dir.glob("*.png")] == [png.name]

    # Renderer changes re-render too (when the file is here)...
    monkeypatch.setattr(cad_sprites, "RENDER_VERSION", 999)
    store = CadSprites(baked_dir, registry, ppi=40)
    store.get("CUBE")
    assert "v999" in store.entry(png.stem).renderer

    # ...but without the file the last bake is used as is.
    stl.unlink()
    monkeypatch.setattr(cad_sprites, "RENDER_VERSION", 1000)
    kept = CadSprites(baked_dir, registry, ppi=40).get("CUBE")
    assert kept.width_in == pytest.approx(bigger.width_in)


def test_prune_removes_unused_sprites(tmp_path):
    rgba = np.full((4, 4, 4), 255, np.uint8)
    for name in ("A-1", "B-2"):
        write_png(tmp_path / f"{name}.png", rgba)
    store = CadSprites(tmp_path, registry={})
    assert store.prune({"A-1"}) == ["B-2"]
    assert [p.name for p in tmp_path.glob("*.png")] == ["A-1.png"]
