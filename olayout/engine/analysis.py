"""One analysis pass over a layout: place units, resolve forks, validate, trace.

Passes are incremental: builds and world geometry are cached, and pedestal
forks are only re-solved near components that changed since the last pass
(cascading when a re-solved fork actually moves), so dragging one part in a
large layout stays interactive.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from olayout.catalog.parts import Layer
from olayout.catalog.units import ForkPlacement, Mounting
from olayout.core.breadboard import Breadboard
from olayout.core.layout import IssueKey, Layout
from olayout.engine.fork import resolve_fork
from olayout.engine.validation import Issue, IssueKind, find_issues
from olayout.engine.world import BuildCache, PlacedUnit
from olayout.optics.tracer import BeamTracer, TraceResult, TraceWarning

log = logging.getLogger(__name__)

TRACE_MARGIN = 8.0  # beams are followed this far beyond the breadboard edge
# Two bases can only touch if their posts are closer than this: each base
# (pedestal + fork) reaches at most ~2.9" from its post.
BASE_INTERACTION = 6.5


@dataclass
class Analysis:
    units: dict[str, PlacedUnit] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)  # every problem found, ignored or not
    trace: TraceResult = field(default_factory=TraceResult)
    ignored: frozenset[IssueKey] = frozenset()  # problems the user accepted (from the layout)

    # Ignored problems are still found and listed, but no longer make a
    # placement illegal or count as problems.
    @property
    def active_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.key not in self.ignored]

    @property
    def ignored_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.key in self.ignored]

    @property
    def active_warnings(self) -> list[TraceWarning]:
        return [w for w in self.trace.warnings if w.key not in self.ignored]

    @property
    def ignored_warnings(self) -> list[TraceWarning]:
        return [w for w in self.trace.warnings if w.key in self.ignored]

    @property
    def illegal_ids(self) -> set[str]:
        return {cid for issue in self.active_issues for cid in issue.component_ids}

    def issues_for(self, component_id: str) -> list[Issue]:
        """Active placement problems involving a component."""
        return [i for i in self.active_issues if component_id in i.component_ids]

    def active_keys_for(self, component_id: str) -> list[IssueKey]:
        """Keys of the active problems and beam warnings involving a component."""
        found = [*self.active_issues, *self.active_warnings]
        return list(dict.fromkeys(item.key for item in found if component_id in item.key.components))


class Analyzer:
    """Keeps caches between passes so interactive edits stay fast.

    Fork choice is deliberately "sticky" (it prefers the previous direction
    so forks do not flicker while you drag), so results depend on the edit
    history; ``incremental=False`` re-solves every fork each pass with the
    same stickiness and must give identical results.
    """

    def __init__(self, incremental: bool = True) -> None:
        self.incremental = incremental
        self.cache = BuildCache()
        self._forks: dict[str, ForkPlacement] = {}  # last fork per pedestal component
        self._state: dict[str, tuple] = {}  # last (x, y, angle, build key) per component
        self._board: Breadboard | None = None

    # ------------------------------------------------------------------ run
    def run(self, layout: Layout) -> Analysis:
        result = Analysis(ignored=frozenset(layout.ignored))
        board = layout.breadboard

        # 1. Build every component (cached by type/orientation/label/params).
        state: dict[str, tuple] = {}
        for comp in layout.components:
            try:
                key = BuildCache.key(comp)
                result.units[comp.id] = self.cache.place(comp, None, key)
                state[comp.id] = (comp.x, comp.y, comp.angle, key)
            except Exception as exc:  # bad type or parameters (e.g. hand-edited file)
                log.warning("could not build %s (%s): %s", comp.id, comp.type, exc)
                result.issues.append(
                    Issue(IssueKind.INVALID, f"{comp.label or comp.id}: cannot build '{comp.type}' ({exc})", (comp.id,))
                )

        # 2. Pedestal forks, re-solved only where something changed.
        changed_at = self._changed_positions(state)
        everything = board != self._board or not self.incremental
        self._resolve_forks(layout, result, changed_at, everything)
        self._state, self._board = state, board

        # 3. Placement legality.
        units = list(result.units.values())
        result.issues += find_issues(units, board)

        # 4. Beams.
        result.trace = self.trace(units, layout)
        return result

    def _changed_positions(self, state: dict[str, tuple]) -> list[tuple[float, float]]:
        """Old and new post positions of every component that changed."""
        points: list[tuple[float, float]] = []
        for cid, now in state.items():
            before = self._state.get(cid)
            if before != now:
                points.append(now[:2])
                if before is not None:
                    points.append(before[:2])
        for cid, before in self._state.items():
            if cid not in state:
                points.append(before[:2])
        return points

    def _resolve_forks(self, layout: Layout, result: Analysis, changed_at: list, everything: bool) -> None:
        board = layout.breadboard
        resolved: dict[str, object] = {}  # full base footprints of forks settled this pass
        for comp in layout.components:
            unit = result.units.get(comp.id)
            if unit is None or unit.unit.mounting is not Mounting.PEDESTAL:
                continue
            previous = self._forks.get(comp.id)
            affected = everything or previous is None or any(
                math.hypot(px - comp.x, py - comp.y) < BASE_INTERACTION for px, py in changed_at
            )
            if affected:
                obstacles = []
                for other in layout.components:
                    other_unit = result.units.get(other.id)
                    if other is comp or other_unit is None:
                        continue
                    if math.hypot(other.x - comp.x, other.y - comp.y) >= BASE_INTERACTION:
                        continue
                    shape = resolved.get(other.id, other_unit.fixed_base_footprint)
                    if not shape.is_empty:
                        obstacles.append(shape)
                previous_angle = previous.angle if previous is not None else None
                fork = resolve_fork(comp, board, obstacles, previous=previous_angle)
                if previous is not None and fork != previous:
                    changed_at.append((comp.x, comp.y))  # neighbours later in the list must follow
            else:
                fork = previous
            self._forks[comp.id] = fork
            placed = PlacedUnit(comp, unit.unit, unit.build, fork)
            result.units[comp.id] = placed
            resolved[comp.id] = placed.footprints[Layer.BASE]
        live = {c.id for c in layout.components}
        for stale in set(self._forks) - live:
            del self._forks[stale]

    # ---------------------------------------------------------------- beams
    @staticmethod
    def trace(units: list[PlacedUnit], layout: Layout) -> TraceResult:
        elements, emitters, edges = [], [], []
        for unit in units:
            optics = unit.world_optics()
            elements += optics.bundle.elements
            emitters += optics.bundle.emitters
            if len(optics.edges):
                edges.append((optics.edges, unit.id))
        if not emitters:
            return TraceResult()
        board = layout.breadboard
        xs = [0.0, board.width] + [e.position[0] for e in emitters]
        ys = [0.0, board.height] + [e.position[1] for e in emitters]
        bounds = (min(xs) - TRACE_MARGIN, min(ys) - TRACE_MARGIN, max(xs) + TRACE_MARGIN, max(ys) + TRACE_MARGIN)
        return BeamTracer(elements, [], bounds, edges=edges).trace(emitters)

