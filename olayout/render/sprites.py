"""Sprite rendering + caching (memory and disk).

A :class:`Sprite` is a premultiplied RGBA image of a part seen from above,
with the part-frame coordinates of its top-left corner so the UI can place
it exactly.  Rendering happens once per part/orientation; afterwards a
sprite drawn from a primitive model is loaded from ``.cache/sprites``.
Sprites rendered from vendor CAD are committed instead, see
:mod:`olayout.render.cad_sprites`.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from olayout.catalog.primitives import height_range
from olayout.render.raster import rasterize_all
from olayout.render.shading import shade

log = logging.getLogger(__name__)

RENDER_VERSION = 3  # bump to invalidate cached sprites after renderer changes
DEFAULT_PPI = 200.0
SUPERSAMPLE = 2
MARGIN_INCHES = 0.12  # room for the soft shadow around the part


@dataclass(frozen=True)
class Sprite:
    rgba: np.ndarray  # (H, W, 4) uint8, premultiplied alpha
    shadow: np.ndarray  # (H, W) float32 alpha in [0, 1]
    x0: float  # part-frame x of the left edge
    y1: float  # part-frame y of the top edge
    ppi: float
    z_top: float  # highest point of the part (inches above its frame origin)

    @property
    def width_in(self) -> float:
        return self.rgba.shape[1] / self.ppi

    @property
    def height_in(self) -> float:
        return self.rgba.shape[0] / self.ppi

    @property
    def rect(self) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) in the part frame."""
        return self.x0, self.y1 - self.height_in, self.x0 + self.width_in, self.y1


def render_sprite(prims, ppi: float = DEFAULT_PPI, supersample: int = SUPERSAMPLE) -> Sprite:
    """Render primitives (already in their final orientation) from above."""
    gbuffer = rasterize_all(prims, ppi * supersample, MARGIN_INCHES)
    rgba, shadow = shade(gbuffer, supersample)
    _, z_top = height_range(prims)
    return Sprite(rgba=rgba, shadow=shadow, x0=gbuffer.x0, y1=gbuffer.y1, ppi=ppi, z_top=z_top)


def render_mesh_sprite(
    vertices: np.ndarray,
    faces: np.ndarray,
    colors: np.ndarray | None,
    ppi: float = DEFAULT_PPI,
    supersample: int = SUPERSAMPLE,
    material: str = "anodized",
) -> Sprite:
    """Render a part-frame triangle mesh (inches) from above.

    Per-face ``colors`` win over ``material``; pass ``None`` to use the material.
    """
    from olayout.render.mesh import allocate_for_mesh, rasterize_mesh

    gbuffer = allocate_for_mesh(vertices, ppi * supersample, MARGIN_INCHES)
    rasterize_mesh(gbuffer, vertices, faces, colors, material=material)
    rgba, shadow = shade(gbuffer, supersample)
    return Sprite(rgba=rgba, shadow=shadow, x0=gbuffer.x0, y1=gbuffer.y1, ppi=ppi, z_top=float(vertices[:, 2].max()))


STRETCH_CAP = 0.3  # inches kept at each end: past the bevels, occlusion and shadow of the end faces


def can_stretch(template_span: tuple[float, float], span: tuple[float, float]) -> bool:
    """Whether a part is long enough (and its template too) to be drawn by stretching."""
    return min(template_span[1] - template_span[0], span[1] - span[0]) >= 2 * STRETCH_CAP


def stretch_sprite(template: Sprite, template_span: tuple[float, float], span: tuple[float, float]) -> Sprite:
    """The sprite of a part that is uniform along x, from the sprite of a short
    piece of it (``template``, solid over ``template_span``): both ends are
    copied and the middle column repeated, out to ``span``.  Any length then
    costs a copy instead of a render.  Needs :func:`can_stretch`."""
    ppi = template.ppi
    (t0, t1), (s0, s1) = template_span, span
    left = int(round((t0 + STRETCH_CAP - template.x0) * ppi))  # template columns kept on the left
    right = int(round((t1 - STRETCH_CAP - template.x0) * ppi))  # first template column kept on the right
    centre = int(round(((t0 + t1) / 2 - template.x0) * ppi))
    middle = max(int(round((s1 - s0 - 2 * STRETCH_CAP) * ppi)), 0)

    def stretched(image: np.ndarray) -> np.ndarray:
        fill = np.repeat(image[:, centre : centre + 1], middle, axis=1)
        return np.ascontiguousarray(np.concatenate([image[:, :left], fill, image[:, right:]], axis=1))

    return Sprite(
        rgba=stretched(template.rgba),
        shadow=stretched(template.shadow),
        x0=s0 - (t0 - template.x0),
        y1=template.y1,
        ppi=ppi,
        z_top=template.z_top,
    )


def model_key(prims, ppi: float) -> str:
    text = f"v{RENDER_VERSION}|{ppi}|{SUPERSAMPLE}|{MARGIN_INCHES}|{prims!r}"
    return hashlib.sha1(text.encode()).hexdigest()[:16]


class SpriteCache:
    """Memory + disk cache of primitive-model sprites keyed by the model itself.

    Any change in a part's primitives (dimensions, materials, orientation)
    changes the key, so stale sprites are never shown.
    """

    def __init__(self, directory: Path | None, ppi: float = DEFAULT_PPI):
        self.directory = directory
        self.ppi = ppi
        self._memory: dict[str, Sprite] = {}
        if directory is not None:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError:
                log.warning("sprite cache directory %s is not writable; caching in memory only", directory)
                self.directory = None

    def get(self, name: str, prims) -> Sprite:
        key = model_key(prims, self.ppi)
        sprite = self._memory.get(key)
        if sprite is None:
            sprite = self._load(name, key) or self._store(name, key, render_sprite(prims, self.ppi))
            self._memory[key] = sprite
        return sprite

    # ------------------------------------------------------------- disk I/O
    def _path(self, name: str, key: str) -> Path | None:
        if self.directory is None:
            return None
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
        return self.directory / f"{safe}-{key}.npz"

    def _load(self, name: str, key: str) -> Sprite | None:
        path = self._path(name, key)
        if path is None or not path.exists():
            return None
        try:
            with np.load(path) as data:
                meta = data["meta"]
                return Sprite(
                    rgba=data["rgba"],
                    shadow=data["shadow"],
                    x0=float(meta[0]),
                    y1=float(meta[1]),
                    ppi=float(meta[2]),
                    z_top=float(meta[3]),
                )
        except Exception:  # corrupt cache file: re-render
            log.warning("discarding unreadable sprite cache %s", path, exc_info=True)
            return None

    def _store(self, name: str, key: str, sprite: Sprite) -> Sprite:
        path = self._path(name, key)
        if path is not None:
            try:
                meta = np.array([sprite.x0, sprite.y1, sprite.ppi, sprite.z_top])
                np.savez_compressed(path, rgba=sprite.rgba, shadow=sprite.shadow, meta=meta)
            except OSError:
                log.warning("could not write sprite cache %s", path, exc_info=True)
        return sprite
