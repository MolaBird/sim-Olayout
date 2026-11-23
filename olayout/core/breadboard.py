"""Optical breadboard: plate size and the 1/4"-20 tapped-hole grid.

The breadboard's lower-left corner is the layout origin.  Holes sit on a
square grid with spacing ``pitch``; the first row/column is ``margin`` in
from the plate edge.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class BreadboardPreset:
    name: str
    width: float
    height: float
    margin: float = 0.5
    description: str = ""


# Thorlabs imperial aluminum breadboards (1/4"-20 taps on 1" centers), plus a
# few common optical-table tops.  Width is the long side, laid out left-right.
PRESETS: dict[str, BreadboardPreset] = {
    p.name: p
    for p in [
        BreadboardPreset("MB12", 12, 12, description='12" x 12" aluminum breadboard'),
        BreadboardPreset("MB1218", 18, 12, description='12" x 18" aluminum breadboard'),
        BreadboardPreset("MB1224", 24, 12, description='12" x 24" aluminum breadboard'),
        BreadboardPreset("MB1824", 24, 18, description='18" x 24" aluminum breadboard'),
        BreadboardPreset("MB2424", 24, 24, description='24" x 24" aluminum breadboard'),
        BreadboardPreset("MB2436", 36, 24, description='24" x 36" aluminum breadboard'),
        BreadboardPreset("MB30", 30, 30, description='30" x 30" aluminum breadboard'),
        BreadboardPreset("Table 3'x4'", 48, 36, margin=1.0, description="36\" x 48\" optical table"),
        BreadboardPreset("Table 4'x6'", 72, 48, margin=1.0, description="48\" x 72\" optical table"),
        BreadboardPreset("Table 4'x8'", 96, 48, margin=1.0, description="48\" x 96\" optical table"),
    ]
}

DEFAULT_PRESET = "MB1824"


@dataclass(frozen=True)
class Breadboard:
    width: float = 24.0
    height: float = 18.0
    pitch: float = 1.0
    margin: float = 0.5
    hole_diameter: float = 0.25
    name: str = DEFAULT_PRESET

    @classmethod
    def from_preset(cls, name: str) -> Breadboard:
        preset = PRESETS[name]
        return cls(width=preset.width, height=preset.height, margin=preset.margin, name=preset.name)

    # ----------------------------------------------------------- hole grid
    @property
    def columns(self) -> int:
        return max(0, math.floor((self.width - 2 * self.margin) / self.pitch + 1e-9) + 1)

    @property
    def rows(self) -> int:
        return max(0, math.floor((self.height - 2 * self.margin) / self.pitch + 1e-9) + 1)

    def hole_xs(self) -> np.ndarray:
        return self.margin + self.pitch * np.arange(self.columns)

    def hole_ys(self) -> np.ndarray:
        return self.margin + self.pitch * np.arange(self.rows)

    def _index(self, value: float, count: int) -> int:
        return int(np.clip(round((value - self.margin) / self.pitch), 0, max(count - 1, 0)))

    def nearest_hole(self, x: float, y: float) -> tuple[float, float]:
        """The grid hole closest to (x, y), clamped onto the board."""
        col = self._index(x, self.columns)
        row = self._index(y, self.rows)
        return self.margin + col * self.pitch, self.margin + row * self.pitch

    def hole_at(self, x: float, y: float, tol: float = 1e-3) -> tuple[int, int] | None:
        """(column, row), both 1-based, of the hole at (x, y), or None."""
        if self.columns == 0 or self.rows == 0:
            return None
        hx, hy = self.nearest_hole(x, y)
        if math.hypot(hx - x, hy - y) > tol:
            return None
        return self._index(x, self.columns) + 1, self._index(y, self.rows) + 1

    def holes_in_annulus(self, cx: float, cy: float, r_min: float, r_max: float) -> np.ndarray:
        """Hole centres whose distance from (cx, cy) lies in [r_min, r_max]."""
        xs = self.hole_xs()
        ys = self.hole_ys()
        xs = xs[(xs >= cx - r_max) & (xs <= cx + r_max)]
        ys = ys[(ys >= cy - r_max) & (ys <= cy + r_max)]
        if xs.size == 0 or ys.size == 0:
            return np.zeros((0, 2))
        gx, gy = np.meshgrid(xs, ys)
        pts = np.column_stack([gx.ravel(), gy.ravel()])
        dist = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
        return pts[(dist >= r_min) & (dist <= r_max)]

    def contains(self, x: float, y: float, pad: float = 0.0) -> bool:
        return -pad <= x <= self.width + pad and -pad <= y <= self.height + pad

    def mounting_counterbores(self) -> list[tuple[float, float]]:
        """Counterbored holes that bolt an MB breadboard down (not part of the grid).

        They sit 1" in from each corner, between grid holes, plus one at the
        centre on boards 18" or longer.  Optical tables have none.
        """
        if self.margin != 0.5 or min(self.width, self.height) < 6:
            return []
        points = [(1.0, 1.0), (self.width - 1.0, 1.0), (1.0, self.height - 1.0), (self.width - 1.0, self.height - 1.0)]
        if max(self.width, self.height) >= 18:
            points.append((self.width / 2, self.height / 2))
        return points

    # -------------------------------------------------------- serialization
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Breadboard:
        defaults = cls()
        if "preset" in data and data["preset"] in PRESETS:  # hand-written shorthand
            return cls.from_preset(data["preset"])
        sized = any(key in data for key in ("width", "height", "pitch", "margin"))
        return cls(
            width=float(data.get("width", defaults.width)),
            height=float(data.get("height", defaults.height)),
            pitch=float(data.get("pitch", defaults.pitch)),
            margin=float(data.get("margin", defaults.margin)),
            hole_diameter=float(data.get("hole_diameter", defaults.hole_diameter)),
            name=str(data.get("name", "Custom" if sized else defaults.name)),
        )
