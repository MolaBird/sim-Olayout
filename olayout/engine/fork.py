"""Choose where a pedestal base's CF125C clamping fork points.

A pedestal base can stand anywhere; its fork swivels 360 degrees around the
BE1 adapter and the captive screw slides along the slot, so the base is
legal when some breadboard hole lies within the slot's reach.  Among the
reachable holes we pick the best one:

* automatic mode prefers forks that do not hit other bases, point behind
  the optic, keep the screw mid-slot, and stay where they were last time
  (so forks don't flicker while you drag things around);
* with a preferred angle set by the user, the hole closest to that
  direction wins.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import shapely
from shapely.geometry import box

from olayout.catalog import parts as P
from olayout.catalog.units import ForkPlacement, default_fork, get_param
from olayout.core.breadboard import Breadboard
from olayout.core.geometry import Pose, transform_geometry
from olayout.core.layout import Component

AUTO_PREFERRED = 180.0  # behind the optic, in the unit frame
OVERLAP_AREA = 1e-4  # square inches


@lru_cache(maxsize=1)
def _fork_shape():
    """Fork + screw head footprint in the fork frame (slot along +x)."""
    return P.get_part("CF125C").footprint()


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def preferred_angle(comp: Component) -> float | None:
    value = get_param(comp.params, "fork.angle", None)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def resolve_fork(
    comp: Component,
    board: Breadboard,
    obstacles: list,
    previous: float | None = None,
) -> ForkPlacement:
    """Pick the fork direction/screw position for one pedestal base.

    ``obstacles`` are world-frame base-layer footprints of *other* components.
    ``previous`` is the angle chosen last time (unit frame), if any.
    """
    manual = preferred_angle(comp)
    preferred = AUTO_PREFERRED if manual is None else manual
    r_lo, r_hi = P.cf125c_screw_range()
    r_mid = (r_lo + r_hi) / 2
    holes = board.holes_in_annulus(comp.x, comp.y, r_lo, r_hi)
    if len(holes) == 0:
        fallback = default_fork()
        return ForkPlacement(angle=preferred, screw_distance=fallback.screw_distance, hole=None)

    tree = shapely.STRtree(obstacles) if obstacles else None
    board_rect = box(0.0, 0.0, board.width, board.height)
    shape = _fork_shape()
    best: tuple[float, ForkPlacement] | None = None
    for hx, hy in holes:
        world_angle = math.degrees(math.atan2(hy - comp.y, hx - comp.x))
        rel = world_angle - comp.angle
        dist = float(np.hypot(hx - comp.x, hy - comp.y))
        fork_world = transform_geometry(shape, Pose(comp.x, comp.y, world_angle))

        overlap = 0.0
        if tree is not None:
            for idx in tree.query(fork_world):
                overlap += fork_world.intersection(obstacles[idx]).area
        off_board = 0.0 if board_rect.contains(fork_world) else fork_world.difference(board_rect).area

        deviation = _angle_diff(rel, preferred)
        if manual is None:
            score = 1000.0 * (overlap > OVERLAP_AREA) + 0.5 * deviation
            if previous is not None:
                score += 0.8 * _angle_diff(rel, previous)
        else:
            score = 50.0 * (overlap > OVERLAP_AREA) + 10.0 * deviation
        score += 300.0 * (off_board > OVERLAP_AREA) + 2.0 * abs(dist - r_mid)

        if best is None or score < best[0]:
            best = (score, ForkPlacement(angle=rel, screw_distance=dist, hole=(float(hx), float(hy))))
    return best[1]
