"""Screw a unit to the table through the slots in its own base ("auto mount").

A unit with ``Mounting.SLOTS`` (e.g. the MC2000B chopper head) has arc slots
in its base plate, centred on its origin.  It is held down by one 1/4"-20
screw per slot, into whatever table hole (grid or drilled) lies under that
slot: the hole's centre must be on the slot's centre line, within the slot's
play (its width minus the screw), and within the arc's span.

* :func:`resolve_slots` finds the hole each slot catches at the unit's pose;
* :func:`slot_spots` finds the positions near a point where *every* slot
  catches a hole at the unit's current angle, so placing and dragging can
  snap there.  Two slots of radii R1, R2 catching holes h1, h2 put the
  unit's origin where the circles of radius R1 about h1 and R2 about h2
  meet, so the candidates are those intersections, for pairs of holes near
  the point.  For the chopper (two opposite 1.5" arcs) on a 1" grid these
  are the points midway between two holes 3" apart.
"""

from __future__ import annotations

import math

from olayout.catalog.units import ArcSlot
from olayout.core.breadboard import Breadboard
from olayout.core.layout import Component

SCREW_DIAMETER = 0.25  # 1/4"-20
HOLE_TOLERANCE = 0.002  # inches: a hole this far off the slot's play still takes the screw


def _play(slot: ArcSlot) -> float:
    return max(slot.width - SCREW_DIAMETER, 0.0) / 2 + HOLE_TOLERANCE


def _local(x: float, y: float, angle: float, point) -> tuple[float, float]:
    """``point`` (world) in the frame of a unit at (x, y, angle)."""
    rad = math.radians(-angle)
    dx, dy = point[0] - x, point[1] - y
    return math.cos(rad) * dx - math.sin(rad) * dy, math.sin(rad) * dx + math.cos(rad) * dy


def _hole_for(slot: ArcSlot, x: float, y: float, angle: float, board: Breadboard) -> tuple[float, float] | None:
    play = _play(slot)
    best: tuple[float, tuple[float, float]] | None = None
    for hx, hy in board.holes_in_annulus(x, y, slot.radius - play, slot.radius + play):
        local = _local(x, y, angle, (hx, hy))
        if slot.catches(local, play):
            off_middle = abs((math.degrees(math.atan2(local[1], local[0])) - slot.middle + 180.0) % 360.0 - 180.0)
            if best is None or off_middle < best[0]:
                best = (off_middle, (float(hx), float(hy)))
    return None if best is None else best[1]


def resolve_slots(comp: Component, slots: tuple[ArcSlot, ...], board: Breadboard) -> tuple:
    """The world hole each slot is screwed into (None where none is under it);
    the one nearest the middle of the slot if it catches several."""
    return tuple(_hole_for(slot, comp.x, comp.y, comp.angle, board) for slot in slots)


def _circle_meets(a, ra: float, b, rb: float) -> list[tuple[float, float]]:
    """Where the circles of radius ``ra`` about ``a`` and ``rb`` about ``b`` meet."""
    d = math.dist(a, b)
    if d < 1e-9 or d > ra + rb + 1e-6 or d < abs(ra - rb) - 1e-6:
        return []
    along = (d * d + ra * ra - rb * rb) / (2 * d)
    across = math.sqrt(max(ra * ra - along * along, 0.0))
    ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
    mx, my = a[0] + along * ux, a[1] + along * uy
    points = [(mx - across * uy, my + across * ux), (mx + across * uy, my - across * ux)]
    return points[:1] if across < 1e-9 else points


def slot_spots(
    comp: Component, slots: tuple[ArcSlot, ...], board: Breadboard, x: float, y: float, reach: float
) -> list[tuple[float, float]]:
    """Positions within ``reach`` of (x, y) where every slot catches a hole at
    the unit's current angle, nearest first."""
    if len(slots) < 2:
        return []
    first, second = slots[0], slots[1]
    holes = board.holes_in_annulus(x, y, 0.0, reach + max(s.radius + _play(s) for s in slots))
    spots: dict[tuple[float, float], float] = {}
    for h1 in holes:
        for h2 in holes:
            for cx, cy in _circle_meets(h1, first.radius, h2, second.radius):
                distance = math.hypot(cx - x, cy - y)
                if distance > reach:
                    continue
                if not (first.catches(_local(cx, cy, comp.angle, h1), _play(first))
                        and second.catches(_local(cx, cy, comp.angle, h2), _play(second))):
                    continue
                if any(_hole_for(s, cx, cy, comp.angle, board) is None for s in slots[2:]):
                    continue
                spots.setdefault((round(cx, 6), round(cy, 6)), distance)
    return [spot for spot, _ in sorted(spots.items(), key=lambda item: item[1])]


def mount_problem(slots: tuple[ArcSlot, ...], holes) -> str | None:
    """Why a slotted unit is not screwed down (None: every slot has its screw)."""
    empty = [slot.name or f"slot {i + 1}" for i, (slot, hole) in enumerate(zip(slots, holes or (None,) * len(slots)))
             if hole is None]
    if not empty:
        return None
    which = " and ".join(empty)
    return (f"no breadboard hole under its {which} slot{'s' if len(empty) > 1 else ''}: centre it midway between "
            f"two holes {2 * slots[0].radius:g}\" apart, its slots across them")
