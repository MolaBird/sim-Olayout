"""Optical elements and beam emitters as seen by the 2D tracer.

These are plain geometry + physics records.  The catalog builds them in a
unit's local frame; :meth:`transformed` moves them into the world.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum

import numpy as np
from shapely.geometry import Polygon

from olayout.core.geometry import Pose, transform_geometry, unit_vector


class OpticKind(str, Enum):
    MIRROR = "mirror"  # reflects from its front side; the back is opaque
    LENS = "lens"  # thin lens, both directions
    SPLITTER = "splitter"  # thin plate / pellicle: reflects R, transmits 1 - R
    WINDOW = "window"  # window or filter: transmits T
    BLOCK = "block"  # beam block / absorber
    NONE = "none"  # empty mount: nothing in the bore

    @property
    def label(self) -> str:
        return {
            "mirror": "Mirror",
            "lens": "Lens",
            "splitter": "Beamsplitter",
            "window": "Window / filter",
            "block": "Beam block",
            "none": "Empty",
        }[self.value]


@dataclass(frozen=True)
class OpticalElement:
    """A thin optical surface: a segment centred at ``center``.

    ``normal_angle`` is the direction of the front-surface normal.  The
    surface extends ``half_aperture`` to either side along the tangent.
    """

    kind: OpticKind
    center: tuple[float, float]
    normal_angle: float
    half_aperture: float
    focal_length: float = math.inf  # lens; + converging
    roc: float = math.inf  # mirror radius of curvature; + concave
    reflectance: float = 0.0  # splitter
    transmittance: float = 1.0  # window / splitter (1 - R by default)
    owner: str = ""  # component id
    name: str = ""

    @property
    def normal(self) -> np.ndarray:
        return unit_vector(self.normal_angle)

    @property
    def tangent(self) -> np.ndarray:
        return unit_vector(self.normal_angle + 90.0)

    @property
    def endpoints(self) -> tuple[np.ndarray, np.ndarray]:
        c = np.asarray(self.center, dtype=float)
        t = self.tangent * self.half_aperture
        return c - t, c + t

    def transformed(self, pose: Pose, owner: str | None = None) -> OpticalElement:
        cx, cy = pose.apply(self.center)
        return replace(
            self,
            center=(float(cx), float(cy)),
            normal_angle=self.normal_angle + pose.angle,
            owner=self.owner if owner is None else owner,
        )


@dataclass(frozen=True)
class Emitter:
    """Where a beam starts: position, direction and Gaussian parameters.

    ``waist_distance`` is how far *downstream* of the emitter the waist lies
    (0: the waist is at the emitter; negative: the beam is already diverging).
    """

    position: tuple[float, float]
    angle: float
    wavelength: float  # inches
    waist_radius: float  # inches
    waist_distance: float = 0.0
    m2: float = 1.0
    power: float = 1.0  # mW
    owner: str = ""
    name: str = ""

    def transformed(self, pose: Pose, owner: str | None = None) -> Emitter:
        px, py = pose.apply(self.position)
        return replace(
            self,
            position=(float(px), float(py)),
            angle=self.angle + pose.angle,
            owner=self.owner if owner is None else owner,
        )


@dataclass(frozen=True)
class Blocker:
    """Opaque geometry at beam height (mount bodies), owned by a component."""

    shape: Polygon
    owner: str = ""

    def transformed(self, pose: Pose, owner: str | None = None) -> Blocker:
        return Blocker(transform_geometry(self.shape, pose), self.owner if owner is None else owner)


@dataclass
class OpticsBundle:
    """Everything a component contributes to beam tracing (world frame)."""

    elements: list[OpticalElement] = field(default_factory=list)
    emitters: list[Emitter] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
