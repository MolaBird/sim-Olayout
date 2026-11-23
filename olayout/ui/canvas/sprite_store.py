"""Qt pixmaps for rendered part sprites (one per part/orientation, cached).

A part is drawn from its vendor CAD file when one is registered in
``assets/cad/cad_parts.json`` (and the optional CAD libraries are
installed); otherwise from its dimensioned primitive model.  Either way the
top view is rendered once and then reused from memory / ``.cache/sprites``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPixmap

from olayout import paths
from olayout.catalog.units import PlacedPart
from olayout.render import cad
from olayout.render.sprites import Sprite, SpriteCache

log = logging.getLogger(__name__)

SHADOW_OPACITY = 0.55


@dataclass(frozen=True)
class QtSprite:
    color: QPixmap
    shadow: QPixmap
    rect: QRectF  # in *item* coordinates of the part frame (y down)
    z_top: float


def _qimage(rgba: np.ndarray) -> QImage:
    rgba = np.ascontiguousarray(rgba)
    h, w, _ = rgba.shape
    image = QImage(rgba.data, w, h, 4 * w, QImage.Format.Format_RGBA8888_Premultiplied)
    return image.copy()  # detach from the numpy buffer


def _to_qt(sprite: Sprite) -> QtSprite:
    shadow_rgba = np.zeros(sprite.shadow.shape + (4,), np.uint8)
    shadow_rgba[..., 3] = np.clip(sprite.shadow * 255.0 * SHADOW_OPACITY, 0, 255).astype(np.uint8)
    x0, y0, x1, y1 = sprite.rect
    # Part frame has y up; Qt item coordinates have y down.
    rect = QRectF(x0, -y1, x1 - x0, y1 - y0)
    return QtSprite(
        color=QPixmap.fromImage(_qimage(sprite.rgba)),
        shadow=QPixmap.fromImage(_qimage(shadow_rgba)),
        rect=rect,
        z_top=sprite.z_top,
    )


class SpriteStore:
    """Process-wide cache: rendered once, then reused by every item."""

    _instance: SpriteStore | None = None

    def __init__(self) -> None:
        self._cache = SpriteCache(paths.sprite_cache_dir())
        self._qt: dict[tuple, QtSprite] = {}
        self._cad_sources = cad.load_registry()
        self._cad_failed: set[str] = set()

    @classmethod
    def instance(cls) -> SpriteStore:
        if cls._instance is None:
            cls._instance = SpriteStore()
        return cls._instance

    @property
    def cad_parts(self) -> list[str]:
        return sorted(self._cad_sources)

    def get(self, part: PlacedPart) -> QtSprite:
        key = (part.part_id, part.prims, part.roll, part.z_offset)
        sprite = self._qt.get(key)
        if sprite is None:
            sprite = _to_qt(self._render(part))
            self._qt[key] = sprite
        return sprite

    def _render(self, part: PlacedPart) -> Sprite:
        source = self._cad_sources.get(part.part_id)
        if source is not None and part.part_id not in self._cad_failed and part.stretch_x == 1.0:
            try:
                return self._cad_sprite(part, source)
            except Exception:  # bad file, tessellation failure...: fall back, never crash
                log.warning("CAD render failed for %s; using the primitive model", part.part_id, exc_info=True)
                self._cad_failed.add(part.part_id)
        return self._cache.get(part.sprite_name, part.prims)

    def _cad_sprite(self, part: PlacedPart, source: cad.CadSource) -> Sprite:
        roll = np.eye(3) if part.roll is None else np.asarray(part.roll, dtype=float)
        identity = f"{source.fingerprint()}|{np.round(roll, 6).tolist()}|{part.z_offset:.6f}"

        def mesh():
            vertices, faces, colors = cad.part_mesh(source)
            vertices = vertices @ roll.T
            vertices[:, 2] += part.z_offset
            return vertices, faces, colors

        return self._cache.get_mesh(f"cad-{part.sprite_name}", identity, mesh)
