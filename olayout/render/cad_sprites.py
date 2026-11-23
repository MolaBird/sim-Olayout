"""CAD sprites, baked into ``assets/sprites`` and committed with the code.

Vendor STEP files are not committed (size, licence), so a fresh clone cannot
render CAD sprites itself.  Every CAD sprite is therefore saved as a PNG with
an entry in ``index.json`` that holds its placement and a *stamp*:

* The **key** (the file name) says what is drawn and where: the part, its
  CAD file name and alignment (axes, origin) and the orientation the part is
  mounted in.  A baked sprite is only ever used for exactly that geometry.
* The **stamp** says how it was drawn: the CAD file's SHA-1 and the renderer
  settings.  With the CAD file present, a stale stamp means a re-render that
  updates the PNG in place (commit it).  Without the file, the baked sprite is
  used as is.

``python -m olayout.tools.bake_sprites`` renders the whole catalog at once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PySide6.QtGui import QImage

from olayout.render import cad
from olayout.render.shading import shadow_from_coverage
from olayout.render.sprites import (
    DEFAULT_PPI,
    MARGIN_INCHES,
    RENDER_VERSION,
    SUPERSAMPLE,
    Sprite,
    render_mesh_sprite,
)

log = logging.getLogger(__name__)

INDEX_FILE = "index.json"
INDEX_COMMENT = (
    "Top-down part sprites rendered from the vendor CAD files in assets/cad, which are not committed. "
    "Regenerate with `python -m olayout.tools.bake_sprites`; the app also refreshes a sprite "
    "whenever its CAD file changes locally."
)


@dataclass(frozen=True)
class BakedEntry:
    """Index record of one baked sprite."""

    part: str
    x0: float  # part-frame x of the image's left edge (inches)
    y1: float  # part-frame y of its top edge
    ppi: float
    z_top: float  # highest point of the part
    source_sha1: str  # the CAD file it was rendered from
    renderer: str  # see renderer_stamp()

    @classmethod
    def from_json(cls, record: dict) -> BakedEntry:
        return cls(
            part=str(record["part"]),
            x0=float(record["x0"]),
            y1=float(record["y1"]),
            ppi=float(record["ppi"]),
            z_top=float(record["z_top"]),
            source_sha1=str(record["source_sha1"]),
            renderer=str(record["renderer"]),
        )


def sprite_key(source: cad.CadSource, roll: np.ndarray, z_offset: float) -> str:
    """File stem of the sprite of ``source`` mounted with ``roll`` and ``z_offset``."""
    numbers = [*source.origin_mm, *np.asarray(roll, dtype=float).ravel(), z_offset]
    # "+ 0.0" turns -0.0 into 0.0, so equal orientations always give equal keys.
    text = "|".join(
        [
            source.part_id,
            source.path.name,
            ",".join(source.axes),
            str(source.hide_below_mm),
            ",".join(f"{round(v, 6) + 0.0:.6f}" for v in numbers),
        ]
    )
    if source.hide_above_mm is not None:  # appended only when set, so older keys stay valid
        text += f"|lid {source.hide_above_mm}"
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in source.part_id)
    return f"{safe_id}-{hashlib.sha1(text.encode()).hexdigest()[:12]}"


def renderer_stamp(source: cad.CadSource, ppi: float) -> str:
    """Everything about *how* a CAD sprite is rendered, apart from the file itself."""
    return (
        f"render v{RENDER_VERSION}, mesh v{cad.MESH_CACHE_VERSION}, {ppi:g} ppi, x{SUPERSAMPLE}, "
        f'margin {MARGIN_INCHES:g}", material {source.material}'
    )


def render_cad_sprite(source: cad.CadSource, roll: np.ndarray, z_offset: float, ppi: float = DEFAULT_PPI) -> Sprite:
    """Render a CAD part mounted with ``roll`` (3x3) and raised by ``z_offset``.

    A part with a hidden lid (``hide_above_mm``) is drawn open while the lid
    is on top; mounted upside down, it shows its real underside.
    """
    vertices, faces, colors = cad.part_mesh(source)
    roll = np.asarray(roll, dtype=float)
    if source.hide_above_mm is not None and roll[2, 2] > 0:
        keep = ~cad.lid_faces(vertices, faces, source.hide_above_mm / 25.4)
        faces, colors = faces[keep], colors[keep]
    vertices = vertices @ roll.T  # a new array: the cached mesh stays intact
    vertices[:, 2] += z_offset
    if source.material is not None:
        return render_mesh_sprite(vertices, faces, None, ppi, material=source.material)
    return render_mesh_sprite(vertices, faces, colors, ppi)


def _roll_matrix(roll) -> np.ndarray:
    return np.eye(3) if roll is None else np.asarray(roll, dtype=float)


class CadSprites:
    """Sprites of the parts registered in ``cad_parts.json``, by mounting orientation."""

    def __init__(
        self,
        directory: Path,
        registry: dict[str, cad.CadSource] | None = None,
        ppi: float = DEFAULT_PPI,
    ):
        self.directory = directory
        self.registry = cad.load_registry() if registry is None else registry
        self.ppi = ppi
        self._index = _read_index(directory / INDEX_FILE)
        self._failed: set[str] = set()  # parts whose CAD file could not be rendered

    def covers(self, part_id: str) -> bool:
        return part_id in self.registry

    def key_for(self, part_id: str, roll=None, z_offset: float = 0.0) -> str | None:
        source = self.registry.get(part_id)
        return None if source is None else sprite_key(source, _roll_matrix(roll), z_offset)

    def entry(self, key: str) -> BakedEntry | None:
        return self._index.get(key)

    def get(self, part_id: str, roll=None, z_offset: float = 0.0) -> Sprite | None:
        """The part's CAD sprite; ``None`` if it has no CAD entry, or no file and nothing baked."""
        source = self.registry.get(part_id)
        if source is None:
            return None
        roll = _roll_matrix(roll)
        key = sprite_key(source, roll, z_offset)
        entry = self._index.get(key)
        baked = self._load(key, entry) if entry is not None else None
        if part_id in self._failed or not source.readable():
            return baked  # no usable CAD file here: the baked sprite, however old
        sha1, renderer = cad.content_sha1(source.path), renderer_stamp(source, self.ppi)
        if baked is not None and (entry.source_sha1, entry.renderer) == (sha1, renderer):
            return baked
        sprite = self._render(source, roll, z_offset)
        if sprite is None:
            return baked
        self._save(key, sprite, BakedEntry(
            part=part_id,
            x0=round(sprite.x0, 6),
            y1=round(sprite.y1, 6),
            ppi=sprite.ppi,
            z_top=round(sprite.z_top, 6),
            source_sha1=sha1,
            renderer=renderer,
        ))
        return sprite

    def prune(self, keep: set[str]) -> list[str]:
        """Delete baked sprites whose key is not in ``keep``; returns the deleted keys."""
        stale = sorted((set(self._index) | {p.stem for p in self.directory.glob("*.png")}) - keep)
        for key in stale:
            self._index.pop(key, None)
            (self.directory / f"{key}.png").unlink(missing_ok=True)
        if stale:
            self._write_index()
        return stale

    # ---------------------------------------------------------------- internals
    def _render(self, source: cad.CadSource, roll: np.ndarray, z_offset: float) -> Sprite | None:
        try:
            return render_cad_sprite(source, roll, z_offset, self.ppi)
        except Exception:  # bad file, tessellation failure...: fall back, never crash
            log.warning("could not render the CAD file of %s", source.part_id, exc_info=True)
            self._failed.add(source.part_id)
            return None

    def _load(self, key: str, entry: BakedEntry) -> Sprite | None:
        rgba = read_png(self.directory / f"{key}.png")
        if rgba is None:
            log.warning("baked sprite %s is missing or unreadable", key)
            return None
        shadow = shadow_from_coverage(rgba[..., 3] / 255.0, entry.ppi)
        return Sprite(rgba=rgba, shadow=shadow, x0=entry.x0, y1=entry.y1, ppi=entry.ppi, z_top=entry.z_top)

    def _save(self, key: str, sprite: Sprite, entry: BakedEntry) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            write_png(self.directory / f"{key}.png", sprite.rgba)
            self._index[key] = entry
            self._write_index()
        except OSError:  # read-only install: the sprite is still used this session
            log.warning("could not save baked sprite %s", key, exc_info=True)

    def _write_index(self) -> None:
        data = {
            "_comment": INDEX_COMMENT,
            "sprites": {key: asdict(entry) for key, entry in sorted(self._index.items())},
        }
        _replace_file(self.directory / INDEX_FILE, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def _read_index(path: Path) -> dict[str, BakedEntry]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data["sprites"]
        if not isinstance(records, dict):
            raise TypeError("'sprites' is not an object")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("ignoring unreadable sprite index %s", path, exc_info=True)
        return {}
    index = {}
    for key, record in records.items():
        try:
            index[key] = BakedEntry.from_json(record)
        except (KeyError, TypeError, ValueError):
            log.warning("ignoring malformed baked sprite %r", key)
    return index


def _replace_file(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (no half-written file on a crash)."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


# PNG files go through Qt's image codecs (PySide6 is a core dependency).
def write_png(path: Path, rgba: np.ndarray) -> None:
    """Save premultiplied uint8 RGBA (H, W, 4) as a PNG."""
    rgba = np.ascontiguousarray(rgba)
    height, width, _ = rgba.shape
    image = QImage(rgba.data, width, height, 4 * width, QImage.Format.Format_RGBA8888_Premultiplied)
    temporary = path.with_name(path.name + ".tmp")
    if not image.save(str(temporary), "PNG", 0):  # quality 0 = smallest file
        raise OSError(f"cannot write {temporary}")
    os.replace(temporary, path)


def read_png(path: Path) -> np.ndarray | None:
    """Load a PNG as premultiplied uint8 RGBA (H, W, 4), or ``None``."""
    image = QImage(str(path))
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    width, height = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), np.uint8, count=image.sizeInBytes()).reshape(height, -1)
    return rows[:, : 4 * width].reshape(height, width, 4).copy()
