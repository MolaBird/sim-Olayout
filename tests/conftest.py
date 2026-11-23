"""Test configuration: headless Qt and isolated settings."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

import pytest  # noqa: E402

from olayout.catalog.units import UNIT_TYPES, new_component_params  # noqa: E402
from olayout.core.layout import Component, Layout  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _isolated_qsettings(tmp_path_factory):
    """Keep tests from touching the user's real settings (recent files etc.)."""
    try:
        from PySide6.QtCore import QSettings
    except ImportError:  # pragma: no cover
        yield
        return
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("settings")))
    yield


def make_component(layout: Layout, type_id: str, x: float, y: float, angle: float = 0.0, **params) -> Component:
    comp = Component(
        id=layout.new_id(),
        type=type_id,
        x=x,
        y=y,
        angle=angle,
        label=layout.new_label(UNIT_TYPES[type_id].label_prefix),
        params=new_component_params(type_id),
    )
    for key, value in params.items():
        comp.params[key] = value
    layout.components.append(comp)
    return comp
