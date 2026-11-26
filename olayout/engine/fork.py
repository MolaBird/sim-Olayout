"""Choose where a clamped post's clamping fork points.

A post holder on a BE1 pedestal can stand anywhere: its clamping fork (a
CF125C, or the short CF038) swivels 360 degrees around the pedestal and the
captive screw slides along the slot, so the post is legal when some
breadboard hole (a grid hole or a drilled one) lies within the slot's reach.
Among the reachable holes we pick the best one:

* automatic mode prefers forks that do not hit other bases, point behind
  the optic, keep the screw mid-slot, and stay where they were last time
  (so forks don't flicker while you drag things around);
* with a direction set by the user (on the table, so it stays put while the
  optic turns), the reachable hole closest to that direction wins.
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import box

from olayout.catalog import parts as P
from olayout.catalog.units import ForkPlacement, clamp_angle, clamp_footprint, clamp_of
from olayout.core.breadboard import Breadboard
from olayout.core.geometry import Pose, transform_geometry
from olayout.core.layout import Component

AUTO_PREFERRED = 180.0  # behind the optic, in the unit frame
OVERLAP_AREA = 1e-4  # square inches


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def preferred_angle(comp: Component) -> float | None:
    """The clamp direction the user set, in the unit frame (None: automatic)."""
    world = clamp_angle(comp)
    return None if world is None else world - comp.angle


def reachable_holes(comp: Component, board: Breadboard) -> np.ndarray:
    """Every hole (grid or drilled) the clamp's screw can reach from this post."""
    r_lo, r_hi = P.clamp_spec(clamp_of(comp)).screw_travel
    return board.holes_in_annulus(comp.x, comp.y, r_lo, r_hi)


def hole_toward(comp: Component, board: Breadboard, angle: float) -> tuple[float, float] | None:
    """The reachable hole closest to the direction ``angle`` (degrees, on the table)."""
    holes = reachable_holes(comp, board)
    if len(holes) == 0:
        return None
    directions = np.degrees(np.arctan2(holes[:, 1] - comp.y, holes[:, 0] - comp.x))
    best = int(np.argmin([_angle_diff(float(d), angle) for d in directions]))
    return float(holes[best, 0]), float(holes[best, 1])


def resolve_fork(
    comp: Component,
    board: Breadboard,
    obstacles: list,
    previous: float | None = None,
) -> ForkPlacement:
    """Pick the fork direction and screw position for one clamped post.

    ``obstacles`` are world-frame base-layer footprints of *other* components.
    ``previous`` is the angle chosen last time (unit frame), if any.
    """
    clamp = clamp_of(comp)
    manual = preferred_angle(comp)
    preferred = AUTO_PREFERRED if manual is None else manual
    r_lo, r_hi = P.clamp_spec(clamp).screw_travel
    r_mid = (r_lo + r_hi) / 2
    holes = board.holes_in_annulus(comp.x, comp.y, r_lo, r_hi)
    if len(holes) == 0:
        return ForkPlacement(angle=preferred, screw_distance=r_mid, hole=None, clamp=clamp)

    tree = shapely.STRtree(obstacles) if obstacles else None
    board_rect = box(0.0, 0.0, board.width, board.height)
    shape = clamp_footprint(clamp)
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
            best = (score, ForkPlacement(angle=rel, screw_distance=dist, hole=(float(hx), float(hy)), clamp=clamp))
    return best[1]
