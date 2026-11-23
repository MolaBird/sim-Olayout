"""Placement legality: overlaps within a layer, holes and board edges."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import shapely
from shapely.geometry import box

from olayout.catalog.parts import Layer
from olayout.catalog.units import Mounting
from olayout.core.breadboard import Breadboard
from olayout.engine.world import PlacedUnit

OVERLAP_AREA = 1e-4  # square inches; touching parts are fine
OFF_BOARD_AREA = 1e-3


class IssueKind(str, Enum):
    OVERLAP = "overlap"  # two components occupy the same space in a layer
    OFF_HOLE = "off-hole"  # a hole-mounted post holder is not on a hole
    NO_HOLE = "no-hole"  # no breadboard hole within the clamping fork's reach
    OFF_BOARD = "off-board"  # a base hangs over the breadboard edge
    INVALID = "invalid"  # the component could not be built (unknown type, bad parameters)


@dataclass(frozen=True)
class Issue:
    kind: IssueKind
    message: str
    component_ids: tuple[str, ...]
    layer: Layer | None = None
    region: object | None = None  # world-frame shapely geometry to highlight


def _name(unit: PlacedUnit) -> str:
    return unit.component.label or unit.unit.name


def find_overlaps(units: list[PlacedUnit], layer: Layer) -> list[Issue]:
    shapes = [(u, u.footprints[layer]) for u in units]
    shapes = [(u, s) for u, s in shapes if not s.is_empty]
    if len(shapes) < 2:
        return []
    geoms = [s for _, s in shapes]
    tree = shapely.STRtree(geoms)
    left, right = tree.query(geoms, predicate="intersects")
    issues = []
    for i, j in zip(left, right):
        if i >= j:
            continue
        overlap = geoms[i].intersection(geoms[j])
        if overlap.area <= OVERLAP_AREA:
            continue
        a, b = shapes[i][0], shapes[j][0]
        where = "bases" if layer is Layer.BASE else "mounts"
        issues.append(
            Issue(
                IssueKind.OVERLAP,
                f"{_name(a)} and {_name(b)}: {where} overlap",
                (a.id, b.id),
                layer,
                overlap,
            )
        )
    return issues


def find_issues(units: list[PlacedUnit], board: Breadboard) -> list[Issue]:
    issues: list[Issue] = []
    for layer in Layer:
        issues += find_overlaps(units, layer)

    board_rect = box(0.0, 0.0, board.width, board.height)
    shapely.prepare(board_rect)  # fast repeated containment tests
    for unit in units:
        comp = unit.component
        mounting = unit.unit.mounting
        if mounting is Mounting.FREE:
            continue
        if mounting is Mounting.HOLE and board.hole_at(comp.x, comp.y) is None:
            issues.append(
                Issue(IssueKind.OFF_HOLE, f"{_name(unit)}: post holder is not on a breadboard hole", (comp.id,))
            )
        if mounting is Mounting.PEDESTAL and (unit.fork is None or unit.fork.hole is None):
            issues.append(
                Issue(IssueKind.NO_HOLE, f"{_name(unit)}: no breadboard hole within the clamping fork's reach", (comp.id,))
            )
        base = unit.footprints[Layer.BASE]
        if not base.is_empty and not board_rect.contains(base):
            outside = base.difference(board_rect)
            if outside.area > OFF_BOARD_AREA:
                issues.append(
                    Issue(
                        IssueKind.OFF_BOARD,
                        f"{_name(unit)}: base hangs over the breadboard edge",
                        (comp.id,),
                        Layer.BASE,
                        outside,
                    )
                )
    return issues
