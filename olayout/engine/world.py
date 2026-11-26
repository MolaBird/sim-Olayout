"""World-space geometry of placed components (with build caching).

Builds depend only on a component's type/orientation/label/params, and the
world-frame geometry of a build depends only on its pose (and fork), so
both are cached: while dragging one part, nothing else is recomputed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np
import shapely

from olayout.catalog.parts import Layer
from olayout.catalog.units import (
    ForkPlacement,
    PlacedPart,
    UnitBuild,
    Mounting,
    UnitType,
    fork_footprint,
    fork_parts,
    get_unit_type,
    mounting_of,
    slot_screw_parts,
)
from olayout.core.geometry import Pose, as_polygonal, polygon_edges, transform_geometry
from olayout.core.layout import Component
from olayout.optics.elements import OpticsBundle

POSE_CACHE_LIMIT = 64  # world results remembered per build (recent poses)


@dataclass
class WorldOptics:
    """A component's optics in the world frame, plus its blocker edges."""

    bundle: OpticsBundle
    edges: np.ndarray  # (N, 2, 2) blocker boundary segments


@dataclass
class PlacedUnit:
    """A component, its cached build and how it is held down where it stands:
    a clamped post's resolved fork, or the holes a slotted base is screwed to."""

    component: Component
    unit: UnitType
    build: UnitBuild
    fork: ForkPlacement | None = None
    screws: tuple | None = None  # a slotted base: the world hole under each slot (None where there is none)

    @property
    def id(self) -> str:
        return self.component.id

    @property
    def pose(self) -> Pose:
        return self.component.pose

    @property
    def mounting(self) -> Mounting:
        """How it is fastened: a post in a hole is ``HOLE``, a clamped one ``PEDESTAL``."""
        return mounting_of(self.component)

    def _memo(self, kind: str, compute, *extra):
        cache = self.build.pose_cache
        key = (kind, self.pose, self.fork, self.screws, *extra)
        value = cache.get(key)
        if value is None:
            if len(cache) >= POSE_CACHE_LIMIT:
                cache.clear()
            value = compute()
            cache[key] = value
        return value

    @cached_property
    def parts(self) -> tuple[PlacedPart, ...]:
        """All parts in the unit frame, including the clamping fork or the screws if any."""
        parts = self.build.parts
        if self.fork is not None:
            parts += fork_parts(self.fork)
        if self.screws:
            parts += slot_screw_parts(self.pose, self.screws)
        return parts

    @cached_property
    def local_footprints(self) -> dict[Layer, object]:
        """Unit-frame footprint per layer, clamping fork included."""
        result = {layer: self.build.footprint(layer) for layer in Layer}
        if self.fork is not None:
            result[Layer.BASE] = as_polygonal(shapely.union_all([result[Layer.BASE], fork_footprint(self.fork)]))
        return result

    @cached_property
    def footprints(self) -> dict[Layer, object]:
        """World-frame footprint per layer."""
        return self._memo(
            "footprints",
            lambda: {layer: transform_geometry(shape, self.pose) for layer, shape in self.local_footprints.items()},
        )

    @cached_property
    def fixed_base_footprint(self):
        """World-frame base footprint without the swivelling clamping fork."""
        build, pose = self.build, self.pose
        cache = build.pose_cache
        key = ("fixed-base", pose)
        if key not in cache:
            cache[key] = transform_geometry(build.footprint(Layer.BASE), pose)
        return cache[key]

    @property
    def is_virtual(self) -> bool:
        """Free-standing sources have no mechanical parts."""
        return not self.build.parts

    def world_optics(self) -> WorldOptics:
        def compute() -> WorldOptics:
            pose, owner = self.pose, self.component.id
            blockers = [b.transformed(pose, owner) for b in self.build.blockers]
            edges = [polygon_edges(b.shape) for b in blockers]
            return WorldOptics(
                bundle=OpticsBundle(
                    elements=[e.transformed(pose, owner) for e in self.build.elements],
                    emitters=[e.transformed(pose, owner) for e in self.build.emitters],
                    blockers=blockers,
                ),
                edges=np.concatenate(edges) if edges else np.zeros((0, 2, 2)),
            )

        return self._memo("optics", compute, self.component.id)

    def optics(self) -> OpticsBundle:
        return self.world_optics().bundle


@dataclass
class BuildCache:
    """Unit builds depend only on type/orientation/label/params, never on the
    pose or the fork, so dragging a component never rebuilds it."""

    max_entries: int = 4000
    _cache: dict[tuple, UnitBuild] = field(default_factory=dict)

    @staticmethod
    def key(comp: Component) -> tuple:
        return (comp.type, comp.orientation, comp.label, json.dumps(comp.params, sort_keys=True, default=str))

    def build(self, comp: Component, key: tuple | None = None) -> UnitBuild:
        key = key or self.key(comp)
        build = self._cache.get(key)
        if build is None:
            if len(self._cache) >= self.max_entries:
                self._cache.clear()
            build = get_unit_type(comp.type).build(comp)
            self._cache[key] = build
        return build

    def place(
        self, comp: Component, fork: ForkPlacement | None = None, key: tuple | None = None, screws: tuple | None = None
    ) -> PlacedUnit:
        return PlacedUnit(comp, get_unit_type(comp.type), self.build(comp, key), fork, screws)
