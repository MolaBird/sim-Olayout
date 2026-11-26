"""Optical breadboard: plate size and the 1/4"-20 tapped-hole grid.

The breadboard's lower-left corner is the layout origin.  Holes sit on a
square grid with spacing ``pitch``; the first row/column is ``margin`` in
from the plate edge.  Extra holes can be drilled, but only at the centre of
a grid cell (midway between four holes), where they clear the grid's own
threads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np


@dataclass(frozen=True)
class BreadboardPreset:
    name: str
    width: float
    height: float
    margin: float = 0.5
    description: str = ""

    @property
    def label(self) -> str:
        return f"{self.name} — {self.description}" if self.description else self.name


# The user's 21" x 20" board first, then Thorlabs imperial aluminum
# breadboards (1/4"-20 taps on 1" centers) and a few optical-table tops.
# Width runs left-right on screen.
PRESETS: dict[str, BreadboardPreset] = {
    p.name: p
    for p in [
        BreadboardPreset("Board 21x20", 21, 20, description='21" wide x 20" deep breadboard'),
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

DEFAULT_PRESET = "Board 21x20"
COUNTERBORE_CLEARANCE = 0.3  # inches: no drilling this close to a mounting counterbore


@dataclass(frozen=True)
class Breadboard:
    width: float = 21.0
    height: float = 20.0
    pitch: float = 1.0
    margin: float = 0.5
    hole_diameter: float = 0.25
    name: str = DEFAULT_PRESET
    drilled: tuple[tuple[float, float], ...] = ()  # extra tapped holes, at cell centres

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

    def nearest_grid_hole(self, x: float, y: float) -> tuple[float, float]:
        """The grid hole closest to (x, y), clamped onto the board."""
        col = self._index(x, self.columns)
        row = self._index(y, self.rows)
        return self.margin + col * self.pitch, self.margin + row * self.pitch

    def nearest_hole(self, x: float, y: float) -> tuple[float, float]:
        """The hole closest to (x, y): a grid hole or a drilled one."""
        best = self.nearest_grid_hole(x, y)
        best_d = math.hypot(best[0] - x, best[1] - y)
        for hole in self.drilled:
            d = math.hypot(hole[0] - x, hole[1] - y)
            if d < best_d:
                best, best_d = hole, d
        return best

    def hole_at(self, x: float, y: float, tol: float = 1e-3) -> tuple[float, float] | None:
        """(column, row), both 1-based, of the hole at (x, y), or None.

        A drilled hole lies between four grid holes, so its numbers end in .5."""
        if self.columns == 0 or self.rows == 0:
            return None
        hx, hy = self.nearest_hole(x, y)
        if math.hypot(hx - x, hy - y) > tol:
            return None
        return _hole_number((hx - self.margin) / self.pitch + 1), _hole_number((hy - self.margin) / self.pitch + 1)

    def holes_in_annulus(self, cx: float, cy: float, r_min: float, r_max: float) -> np.ndarray:
        """Hole centres (grid and drilled) whose distance from (cx, cy) lies in [r_min, r_max]."""
        xs = self.hole_xs()
        ys = self.hole_ys()
        xs = xs[(xs >= cx - r_max) & (xs <= cx + r_max)]
        ys = ys[(ys >= cy - r_max) & (ys <= cy + r_max)]
        pts = np.zeros((0, 2))
        if xs.size and ys.size:
            gx, gy = np.meshgrid(xs, ys)
            pts = np.column_stack([gx.ravel(), gy.ravel()])
        if self.drilled:
            pts = np.vstack([pts, np.asarray(self.drilled, dtype=float)])
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

    # ------------------------------------------------------- drilled holes
    def nearest_cell_centre(self, x: float, y: float) -> tuple[float, float] | None:
        """The centre of the grid cell around (x, y): the only place a hole may be drilled."""
        if self.columns < 2 or self.rows < 2:
            return None
        col = int(np.clip(math.floor((x - self.margin) / self.pitch), 0, self.columns - 2))
        row = int(np.clip(math.floor((y - self.margin) / self.pitch), 0, self.rows - 2))
        return self.margin + (col + 0.5) * self.pitch, self.margin + (row + 0.5) * self.pitch

    def drilled_at(self, x: float, y: float, tol: float = 1e-3) -> tuple[float, float] | None:
        """The drilled hole at (x, y), if there is one."""
        for hole in self.drilled:
            if math.hypot(hole[0] - x, hole[1] - y) <= tol:
                return hole
        return None

    def drill_problem(self, x: float, y: float) -> str | None:
        """Why no hole can be drilled at (x, y), or None if it can."""
        centre = self.nearest_cell_centre(x, y)
        if centre is None or math.hypot(centre[0] - x, centre[1] - y) > 1e-3:
            return "holes can only be drilled at the centre of a grid cell"
        if self.drilled_at(x, y) is not None:
            return "a hole is drilled here already"
        if any(math.hypot(cx - x, cy - y) < COUNTERBORE_CLEARANCE for cx, cy in self.mounting_counterbores()):
            return "a counterbored mounting hole is here"
        return None

    def with_drilled(self, x: float, y: float) -> Breadboard:
        """This board with a hole drilled at (x, y); unchanged if it can't be."""
        if self.drill_problem(x, y) is not None:
            return self
        return replace(self, drilled=tuple(sorted({*self.drilled, (round(x, 6), round(y, 6))})))

    def without_drilled(self, x: float, y: float) -> Breadboard:
        """This board with the drilled hole at (x, y) filled in."""
        hole = self.drilled_at(x, y)
        return self if hole is None else replace(self, drilled=tuple(h for h in self.drilled if h != hole))

    # -------------------------------------------------------- serialization
    def to_dict(self) -> dict:
        data = {
            "width": self.width,
            "height": self.height,
            "pitch": self.pitch,
            "margin": self.margin,
            "hole_diameter": self.hole_diameter,
            "name": self.name,
        }
        if self.drilled:
            data["drilled"] = [[x, y] for x, y in self.drilled]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Breadboard:
        defaults = cls()
        if "preset" in data and data["preset"] in PRESETS:  # hand-written shorthand
            return cls.from_preset(data["preset"])
        sized = any(key in data for key in ("width", "height", "pitch", "margin"))
        board = cls(
            width=float(data.get("width", defaults.width)),
            height=float(data.get("height", defaults.height)),
            pitch=float(data.get("pitch", defaults.pitch)),
            margin=float(data.get("margin", defaults.margin)),
            hole_diameter=float(data.get("hole_diameter", defaults.hole_diameter)),
            name=str(data.get("name", "Custom" if sized else defaults.name)),
        )
        drilled = data.get("drilled", [])
        for point in drilled if isinstance(drilled, list) else []:
            try:
                x, y = (float(v) for v in point)
            except (TypeError, ValueError):
                continue
            board = board.with_drilled(x, y)  # quietly skips anything not at a free cell centre
        return board


def _hole_number(value: float) -> float | int:
    """A 1-based hole number: whole for grid holes, x.5 for drilled ones."""
    halves = round(value * 2) / 2
    return int(halves) if halves == int(halves) else halves
