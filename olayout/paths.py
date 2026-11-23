"""Filesystem locations: caches, autosave, logs, bundled examples."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
EXAMPLES_DIR = PROJECT_ROOT / "examples"
CAD_DIR = PROJECT_ROOT / "assets" / "cad"
BAKED_SPRITES_DIR = PROJECT_ROOT / "assets" / "sprites"  # committed CAD sprites


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


@lru_cache(maxsize=1)
def cache_dir() -> Path:
    """``$OLAYOUT_CACHE``, else ``<project>/.cache``, else ``~/.olayout``."""
    candidates = []
    if os.environ.get("OLAYOUT_CACHE"):
        candidates.append(Path(os.environ["OLAYOUT_CACHE"]))
    candidates += [PROJECT_ROOT / ".cache", Path.home() / ".olayout"]
    for candidate in candidates:
        if _writable(candidate):
            return candidate
    return candidates[-1]


def sprite_cache_dir() -> Path:
    return cache_dir() / "sprites"


def autosave_path() -> Path:
    return cache_dir() / "autosave.olayout.json"


def session_marker_path() -> Path:
    """Exists while the app runs; left behind after a crash."""
    return cache_dir() / "session.lock"


def log_path() -> Path:
    return cache_dir() / "olayout.log"
