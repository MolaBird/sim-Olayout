"""Vendor CAD (STEP/STL/GLB) -> part-frame triangle meshes for sprite rendering.

The canvas never shows 3D: a CAD file is tessellated once (cached as a
compressed mesh), placed in the part frame by an entry in
``assets/cad/cad_parts.json``, and rendered once into a top-down sprite.
Collision footprints and beam blocking still come from the dimensioned
primitive models, so a missing CAD file only changes how a part looks.
The rendered sprites are committed (see :mod:`olayout.render.cad_sprites`),
so clones without the vendor files still draw the real parts.

Registry entry::

    "KM100": {
        "file": "KM100-E02-Step.step",
        "axes": ["z", "x", "y"],             # part x, y, z = these signed CAD axes
        "origin_mm": [5.57, 16.33, -14.72]   # CAD point that becomes the part origin
    }

STEP tessellation needs the optional ``cascadio`` and ``trimesh`` packages;
STL/GLB/OBJ need only ``trimesh``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from olayout import paths
from olayout.render.materials import material_index

log = logging.getLogger(__name__)

REGISTRY_FILE = "cad_parts.json"
MESH_CACHE_VERSION = 1
_AXIS = {"x": 0, "y": 1, "z": 2}


@dataclass(frozen=True)
class CadSource:
    part_id: str
    path: Path
    axes: tuple[str, str, str]
    origin_mm: tuple[float, float, float]
    hide_below_mm: float | None = None  # optional: drop geometry below this part-frame z (mm)
    material: str | None = None  # optional renderer material instead of the file's colours

    def rotation(self) -> np.ndarray:
        """3x3 matrix taking CAD coordinates to part-frame axes."""
        rot = np.zeros((3, 3))
        for row, spec in enumerate(self.axes):
            sign = -1.0 if spec.startswith("-") else 1.0
            rot[row, _AXIS[spec.lstrip("+-")]] = sign
        if abs(abs(np.linalg.det(rot)) - 1.0) > 1e-9:
            raise ValueError(f"{self.part_id}: axes {self.axes} are not a valid axis permutation")
        return rot

    @property
    def is_step(self) -> bool:
        return self.path.suffix.lower() in (".step", ".stp")

    def readable(self) -> bool:
        """The file is here and the libraries needed to read it are installed."""
        if not self.path.exists():
            return False
        has_trimesh, has_cascadio = libraries_available()
        return has_trimesh and (has_cascadio or not self.is_step)

    def fingerprint(self) -> str:
        """Identity of the tessellated mesh (file + placement)."""
        stat = self.path.stat()
        text = f"{self.path.name}|{stat.st_size}|{int(stat.st_mtime)}|{self.axes}|{self.origin_mm}|{self.hide_below_mm}"
        return hashlib.sha1(text.encode()).hexdigest()[:16]


_SHA1_MEMO: dict[tuple[Path, int, int], str] = {}


def content_sha1(path: Path) -> str:
    """SHA-1 of a file's bytes (memoized while its size and mtime stay put)."""
    stat = path.stat()
    memo_key = (path, stat.st_size, stat.st_mtime_ns)
    digest = _SHA1_MEMO.get(memo_key)
    if digest is None:
        sha1 = hashlib.sha1()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                sha1.update(chunk)
        digest = _SHA1_MEMO[memo_key] = sha1.hexdigest()
    return digest


def cad_dir() -> Path:
    return paths.CAD_DIR


@lru_cache(maxsize=1)
def libraries_available() -> tuple[bool, bool]:
    """(trimesh importable, cascadio importable)."""
    try:
        import trimesh  # noqa: F401

        has_trimesh = True
    except Exception:
        has_trimesh = False
    try:
        import cascadio  # noqa: F401

        has_cascadio = True
    except Exception:
        has_cascadio = False
    return has_trimesh, has_cascadio


@lru_cache(maxsize=1)
def load_registry() -> dict[str, CadSource]:
    """Every well-formed CAD entry, whether or not its file is on this machine.

    Use :meth:`CadSource.readable` before tessellating; without the file the
    committed sprite (if any) is still used.
    """
    registry_path = cad_dir() / REGISTRY_FILE
    if not registry_path.exists():
        return {}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("cannot read %s", registry_path, exc_info=True)
        return {}
    sources: dict[str, CadSource] = {}
    for part_id, entry in data.items():
        if part_id.startswith("_") or not isinstance(entry, dict):
            continue
        try:
            source = CadSource(
                part_id=part_id,
                path=cad_dir() / entry["file"],
                axes=tuple(entry.get("axes", ("x", "y", "z"))),
                origin_mm=tuple(float(v) for v in entry.get("origin_mm", (0, 0, 0))),
                hide_below_mm=entry.get("hide_below_mm"),
                material=entry.get("material"),
            )
            source.rotation()
            if source.material is not None:
                material_index(source.material)  # unknown names are rejected here
        except (KeyError, TypeError, ValueError):
            log.warning("ignoring malformed CAD entry %r", part_id, exc_info=True)
            continue
        if source.path.exists() and not source.readable():
            log.info("CAD file for %s found but trimesh/cascadio is not installed; "
                     "using its committed sprite", part_id)
        sources[part_id] = source
    return sources


def _tessellate(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read a CAD file into (vertices in mm, faces, per-face RGB in [0, 1])."""
    import trimesh

    target = path
    if path.suffix.lower() in (".step", ".stp"):
        import cascadio

        target = paths.cache_dir() / "cad" / (path.stem + ".glb")
        target.parent.mkdir(parents=True, exist_ok=True)
        cascadio.step_to_glb(str(path), str(target), tol_linear=0.02, tol_angular=0.3)
    loaded = trimesh.load(str(target))
    meshes = []
    if isinstance(loaded, trimesh.Scene):
        for node in loaded.graph.nodes_geometry:
            transform, geom_name = loaded.graph[node]
            meshes.append(loaded.geometry[geom_name].copy().apply_transform(transform))
    else:
        meshes.append(loaded)
    verts, faces, colors, offset = [], [], [], 0
    for mesh in meshes:
        try:
            rgb = np.asarray(mesh.visual.face_colors)[:, :3] / 255.0
        except Exception:
            rgb = np.full((len(mesh.faces), 3), 0.25)
        verts.append(np.asarray(mesh.vertices, dtype=np.float64))
        faces.append(np.asarray(mesh.faces, dtype=np.int64) + offset)
        colors.append(rgb)
        offset += len(mesh.vertices)
    vertices = np.concatenate(verts)
    # GLB is in metres by convention; STL/OBJ from vendors are usually mm.
    scale = 1000.0 if target.suffix.lower() in (".glb", ".gltf") else 1.0
    return vertices * scale, np.concatenate(faces), np.concatenate(colors).astype(np.float32)


def part_mesh(source: CadSource) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The CAD mesh in the part frame (inches), tessellated once and cached."""
    cache = paths.cache_dir() / "cad" / f"{source.part_id}-{source.fingerprint()}-v{MESH_CACHE_VERSION}.npz"
    if cache.exists():
        try:
            with np.load(cache) as data:
                return data["vertices"], data["faces"], data["colors"]
        except Exception:
            log.warning("discarding unreadable mesh cache %s", cache)
    vertices_mm, faces, colors = _tessellate(source.path)
    rot = source.rotation()
    vertices = (vertices_mm - np.asarray(source.origin_mm)) @ rot.T / 25.4
    if source.hide_below_mm is not None:
        keep_v = vertices[:, 2] >= source.hide_below_mm / 25.4
        keep_f = keep_v[faces].all(axis=1)
        faces, colors = faces[keep_f], colors[keep_f]
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, vertices=vertices, faces=faces, colors=colors)
    except OSError:
        log.warning("could not cache mesh %s", cache)
    return vertices, faces, colors
