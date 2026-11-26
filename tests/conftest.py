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

from olayout.catalog.units import UNIT_TYPES, upgrade_component, with_defaults  # noqa: E402
from olayout.core.layout import Component, Layout  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _isolated_settings(tmp_path_factory):
    """Keep tests out of the user's real preferences (recent files, view toggles)."""
    from olayout.ui.settings import ENV_VAR

    os.environ[ENV_VAR] = str(tmp_path_factory.mktemp("settings") / "olayout.ini")
    yield
    os.environ.pop(ENV_VAR, None)


def make_component(layout: Layout, type_id: str, x: float, y: float, angle: float = 0.0, **params) -> Component:
    """A component with default parameters (``params`` replace whole groups).

    ``type_id`` may be a unit type from before posts were split from their
    mounts (e.g. "km-mirror"): it is upgraded as an old file would be, so a
    post-mounted type given without its post is on the holder it used to have."""
    comp = upgrade_component(Component(id=layout.new_id(), type=type_id, x=x, y=y, angle=angle))
    comp.params = with_defaults(comp.type, comp.params)
    comp.label = layout.new_label(UNIT_TYPES[comp.type].label_prefix)
    for key, value in params.items():
        comp.params[key] = value
    layout.components.append(comp)
    return comp


def on_post(type_id: str, comp_id: str = "c", label: str = "", *, holder: str = "hole", kb1x1: bool = False,
            **fields) -> Component:
    """A post-mounted component (not in any layout): ``holder`` "hole" or "clamped", optionally on a KB1X1."""
    from olayout.catalog.units import new_component_params, set_param

    params = new_component_params(type_id)
    set_param(params, "base.kind", holder)
    set_param(params, "kb1x1.present", kb1x1)
    return Component(comp_id, type_id, label=label, params=params, **fields)
