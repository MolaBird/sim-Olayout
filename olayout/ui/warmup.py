"""Render every part sprite up front (with a progress dialog on a cold cache).

Sprites are cached on disk (CAD ones are committed), so after the first
launch this takes a fraction of a second and the dialog never appears.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProgressDialog

from olayout.catalog.units import catalog_parts
from olayout.ui.canvas.sprite_store import SpriteStore

log = logging.getLogger(__name__)


def warm_sprite_cache() -> None:
    parts = catalog_parts()
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
