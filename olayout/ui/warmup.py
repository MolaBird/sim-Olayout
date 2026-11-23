"""Render every part sprite up front (with a progress dialog on a cold cache).

Sprites are cached on disk, so after the first launch this takes a fraction
of a second and the dialog never appears.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProgressDialog

from olayout.catalog.units import UNIT_TYPES, Mounting, default_fork, new_component_params
from olayout.core.layout import Component
from olayout.engine.world import BuildCache
from olayout.ui.canvas.sprite_store import SpriteStore

log = logging.getLogger(__name__)


def _all_parts():
    cache = BuildCache()
    seen: dict[tuple, object] = {}
    for unit in UNIT_TYPES.values():
        orientations = [name for name, _ in unit.orientations()] or [""]
        for orientation in orientations:
            comp = Component("warm", unit.id, orientation=orientation, params=new_component_params(unit.id))
            fork = default_fork() if unit.mounting is Mounting.PEDESTAL else None
            for part in cache.place(comp, fork).parts:
                seen.setdefault((part.part_id, part.prims, part.roll, part.z_offset), part)
    return list(seen.values())


def warm_sprite_cache() -> None:
    parts = _all_parts()
    dialog = QProgressDialog("Rendering part models (first run only)…", None, 0, len(parts))
    dialog.setWindowTitle("Optical Layout")
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setMinimumDuration(400)
    store = SpriteStore.instance()
    for index, part in enumerate(parts):
        dialog.setValue(index)
        dialog.setLabelText(f"Rendering {part.part_id} (first run only)…")
        QApplication.processEvents()
        try:
            store.get(part)
        except Exception:  # a bad model must never stop the app from starting
            log.exception("could not render %s", part.part_id)
    dialog.setValue(len(parts))
    dialog.close()
