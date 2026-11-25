"""Individual Thorlabs parts, modelled with simple 3D primitives.

Part frames
-----------
* Optic-layer parts (mounts, cage plates, kinematic bases): origin on the
  optic axis, directly above the primary mounting tap; +x points out of the
  front face (toward the light hitting a mirror); +y is your right when you
  face the optic; z up.  The beam plane is z = 0.
* Base-layer parts (post holders, pedestal adapters, clamping forks): origin
  on the table surface at the post axis, z up.

Dimensions are inches from Thorlabs mechanical drawings (KM100 rev C,
KM100CP, KM100BP rev A, FMP1/M, CP33/CP35, SM1ZB rev B, PH1/PH4, TS25H,
CF125C, TR2, MB1224), checked against the vendor STEP files registered in
``assets/cad/cad_parts.json`` (cross-sections at beam height and overlays of
the top-view silhouettes).  Hidden internals are simplified; values not on
a drawing or in a STEP file are marked "estimate".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Polygon, box

from olayout.catalog.primitives import (
    Bore,
    Box,
    Cylinder,
    Primitive,
    Prism,
    Vec3,
    footprint,
    rotation_matrix,
    slice_at,
    transform_all,
)


class Layer(str, Enum):
    """Parts at table level and parts at beam height never collide with each other."""

    BASE = "base"
    OPTIC = "optic"


@dataclass(frozen=True)
class MountingTap:
    """A tapped (or #8 clearance) hole a post or adapter attaches to.

    ``yaw`` (0 or 180) additionally turns the part end-for-end about the
    post, for parts that work facing either way (e.g. the SM1ZB, whose
    fiber can enter from either face).
    """

    name: str
    label: str
    position: Vec3  # centre of the hole on the part's face (part frame)
    normal: Vec3  # outward normal of that face, axis-aligned
    yaw: int = 0


@dataclass(frozen=True)
class OpticSlot:
    """Where a mount holds its optic along the optic axis (x).

    * ``"front"``: the optic drops in from the front onto a rear lip at
      ``seat_x``; its front surface ends up at ``seat_x + thickness``.
    * ``"rear"``: the optic is pressed against a front lip; its front
      surface sits at ``seat_x`` whatever its thickness.
    * ``"centered"``: the optic is centred on the plane ``seat_x`` (a lens in
      a cage plate, or a pellicle membrane of ~zero thickness).
    """

    seat_x: float
    loading: str = "front"
    diameter: float = 1.0
    clear_aperture: float = 0.94

    def front_surface_x(self, thickness: float) -> float:
        if self.loading == "front":
            return self.seat_x + thickness
        if self.loading == "centered":
            return self.seat_x + thickness / 2
        return self.seat_x


@dataclass(frozen=True)
class OrientedPart:
    """A part rolled about its optic axis so that one tap faces the post."""

    prims: tuple[Primitive, ...]
    rotation: np.ndarray  # the roll applied (3x3)
    post_xy: tuple[float, float]  # where the post axis meets the part (part frame)
    tap_z: float  # height of the tap face relative to the beam plane


@dataclass(frozen=True)
class PartSpec:
    id: str
    name: str
    layer: Layer
    prims: tuple[Primitive, ...]
    taps: tuple[MountingTap, ...] = ()
    optic_slot: OpticSlot | None = None
    description: str = ""

    def tap(self, name: str | None) -> MountingTap:
        if not self.taps:
            raise ValueError(f"{self.id} has no mounting taps")
        if not name:
            return self.taps[0]
        for tap in self.taps:
            if tap.name == name:
                return tap
        raise KeyError(f"{self.id} has no tap named {name!r}")

    def oriented(self, tap_name: str | None = None) -> OrientedPart:
        """Roll the part about its optic axis so ``tap_name`` faces the post
        (then turn it end-for-end if the tap asks for it)."""
        tap = self.tap(tap_name)
        rot = rotation_matrix("z", tap.yaw) @ roll_bringing_down(tap.normal)
        pos = rot @ np.asarray(tap.position, dtype=float)
        return OrientedPart(
            prims=transform_all(self.prims, rot),
            rotation=rot,
            post_xy=(float(pos[0]), float(pos[1])),
            tap_z=float(pos[2]),
        )

    def footprint(self):
        return footprint(self.prims)

    def beam_section(self, z: float = 0.0):
        return slice_at(self.prims, z)


def roll_bringing_down(normal: Vec3) -> np.ndarray:
    """Rotation about the optic axis (x) that turns a face normal straight down."""
    target = np.array([0.0, 0.0, -1.0])
    for deg in (0, 90, 180, 270):
        rot = rotation_matrix("x", deg)
        if np.allclose(rot @ np.asarray(normal, dtype=float), target):
            return rot
    raise ValueError(f"tap normal {normal} cannot face down by rolling about the optic axis")


# ==========================================================================
# Kinematic mount family (KM100 rev C drawing; KM100CP/BP share plates/knobs)
# ==========================================================================
KM_PLATE_Y = (-0.965, 0.95)  # front plate, relative to the optic centre
KM_BACK_Y = (-0.965, 1.00)  # back plate (its +y face carries the side hole)
KM_PLATE_Z = (-1.00, 0.97)  # plates are 1.97" tall, optic 1.00" above the bottom
KM_KNOBS = ((0.735, 0.75), (-0.75, -0.735))  # (y, z) of the two adjusters
KM_BACK_ARM = 0.48  # the back plate is an L: 0.48" arms along the bottom and the +y side


def _adjuster(y: float, z: float, x_back: float) -> list[Primitive]:
    """1/4"-80 adjuster sticking out 0.68" behind the back plate (toward -x):
    Ø0.25 screw for 0.23", a Ø0.46 hub, then the Ø0.58 knurled knob."""
    return [
        Cylinder("x", (x_back - 0.115, y, z), 0.125, 0.23, "brass"),
        Cylinder("x", (x_back - 0.29, y, z), 0.23, 0.12, "knob"),
        Cylinder("x", (x_back - 0.515, y, z), 0.29, 0.33, "knob", knurled=True),
    ]


def _l_plate(
    x: tuple[float, float], y: tuple[float, float], z: tuple[float, float], arm: float, material: str
) -> list[Primitive]:
    """An L-shaped plate: one arm along the bottom, one up the +y side.

    The region around the optic axis stays open, so beams transmitted through
    a beamsplitter can leave the mount (unless they walk into the side arm).
    """
    return [
        Box((x[0], y[0], z[0]), (x[1], y[1], z[0] + arm), material),  # bottom arm
        Box((x[0], y[1] - arm, z[0]), (x[1], y[1], z[1]), material),  # +y side arm
    ]


def _kinematic_body(
    *,
    front: list[Primitive],
    back_x: tuple[float, float],
    back_y: tuple[float, float] = KM_BACK_Y,
    plate_z: tuple[float, float] = KM_PLATE_Z,
    back_arm: float = KM_BACK_ARM,
    front_x0: float,
    knobs: tuple[tuple[float, float], ...] = KM_KNOBS,
) -> list[Primitive]:
    """Front plate (given) + L-shaped back plate + springs + adjusters."""
    prims: list[Primitive] = list(front)
    prims += _l_plate(back_x, back_y, plate_z, back_arm, "anodized_light")
    # Springs and the pivot ball sit at the corner of the L; a low web keeps it one body.
    prims.append(
        Box((back_x[1] - 0.01, back_y[1] - back_arm + 0.05, plate_z[0] + 0.05),
            (front_x0 + 0.01, back_y[1] - 0.05, plate_z[0] + back_arm - 0.05), "anodized")
    )
    for y, z in knobs:
        prims += _adjuster(y, z, back_x[0])
    return prims


def make_km100() -> PartSpec:
    # Rev C + STEP: main face at x=0.58 (pads 0.63), front plate back at 0.30,
    # 0.10" gap, L-shaped back plate -0.20..0.20; the #8 holes are mid back plate.
    front = [
        Box((0.30, KM_PLATE_Y[0], KM_PLATE_Z[0]), (0.58, KM_PLATE_Y[1], KM_PLATE_Z[1]), "anodized"),
        Bore("x", (0.46, 0.0, 0.0), 0.50, 0.30),  # Ø1" seat, 0.245" deep
        Bore("x", (0.31, 0.0, 0.0), 0.47, 0.07),  # rear lip, Ø0.94 clear aperture
    ]
    prims = _kinematic_body(front=front, back_x=(-0.20, 0.20), front_x0=0.30)
    taps = (
        MountingTap("bottom", "Upright (bottom hole)", (0.0, 0.0, KM_PLATE_Z[0]), (0, 0, -1)),
        MountingTap("side", "On its side (side hole, mirrored)", (0.0, KM_BACK_Y[1], 0.0), (0, 1, 0)),
    )
    return PartSpec(
        id="KM100",
        name='KM100 kinematic mirror mount, Ø1"',
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=taps,
        optic_slot=OpticSlot(seat_x=0.335, loading="front", clear_aperture=0.94),
        description="Kinematic mirror mount for Ø1\" optics; #8 holes under the optic and on the side.",
    )


def make_km100cp() -> PartSpec:
    """Post-centred KM100: both tap axes pass through the mirror's front surface.

    Origin = mirror front-surface centre; rear-loaded cell with a ~0.08" lip.
    At beam height the front plate only spans y -0.61..0.83 (STEP section).
    """
    front = [
        Box((-0.20, KM_PLATE_Y[0], 0.35), (0.08, KM_PLATE_Y[1], KM_PLATE_Z[1]), "anodized"),
        Box((-0.20, -0.61, -0.35), (0.08, 0.825, 0.35), "anodized"),
        Box((-0.20, KM_PLATE_Y[0], KM_PLATE_Z[0]), (0.08, KM_PLATE_Y[1], -0.35), "anodized"),
        Bore("x", (-0.10, 0.0, 0.0), 0.50, 0.22),  # Ø1" cell, 0.20" deep
        Bore("x", (0.04, 0.0, 0.0), 0.47, 0.12),  # front lip, Ø0.94 clear aperture
    ]
    prims = _kinematic_body(front=front, back_x=(-0.70, -0.30), back_arm=0.41, front_x0=-0.20)
    prims += [
        Box((-0.70, -0.375, -1.25), (0.20, 0.375, KM_PLATE_Z[0]), "anodized_light"),  # foot
        Box((-0.58, 1.00, -0.375), (0.20, 1.25, 0.375), "anodized_light"),  # side ear with the 8-32 tap
    ]
    taps = (
        MountingTap("bottom", "Upright (foot tap)", (0.0, 0.0, -1.25), (0, 0, -1)),
        MountingTap("side", "On its side (ear tap, mirrored)", (0.0, 1.25, 0.0), (0, 1, 0)),
    )
    return PartSpec(
        id="KM100CP",
        name='KM100CP kinematic mount, post-centred, Ø1"',
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=taps,
        optic_slot=OpticSlot(seat_x=0.0, loading="rear", clear_aperture=0.94),
        description="Kinematic mirror mount whose 8-32 taps sit directly under the mirror surface.",
    )


# ==========================================================================
# Pellicle beamsplitters and their mounts (vendor STEP files)
# ==========================================================================
# A BP108 (Ø1") or BP208 (Ø2") pellicle is a 2 µm membrane stretched over the
# front face of a 3/16"-thick ring.  The ring drops into a mount: the KM100BP /
# KM200BP kinematic mounts (a counterbore in the front plate, on a lip) or the
# BP107 / BP207 fixed cradles (a U whose two front tabs clamp the ring).  The
# beam meets the membrane, on the ring's front face.
PELLICLE_FRAME_T = 0.1875  # BP108 / BP208 ring thickness (STEP)
BP108_REFLECTANCE = 0.08  # 8:92 uncoated pellicle, 400-2400 nm


@dataclass(frozen=True)
class PellicleSpec:
    """A pellicle ring: its part id, outer radius and membrane (clear) radius."""

    part_id: str
    outer_radius: float
    membrane_radius: float

    @property
    def diameter(self) -> float:
        return 2 * self.membrane_radius


BP108 = PellicleSpec("BP108", 0.69, 0.50)  # Ø1.38" ring, Ø1" membrane
BP208 = PellicleSpec("BP208", 1.19, 1.00)  # Ø2.38" ring, Ø2" membrane


def _make_pellicle(spec: PellicleSpec, name: str) -> PartSpec:
    """Origin = membrane centre, on the ring's front face; the ring extends
    behind it (toward -x) into the mount."""
    t = PELLICLE_FRAME_T
    prims = (
        Cylinder("x", (-t / 2, 0.0, 0.0), spec.outer_radius, t, "anodized"),
        Bore("x", (-t / 2, 0.0, 0.0), spec.membrane_radius, t + 0.1),
    )
    return PartSpec(
        id=spec.part_id,
        name=name,
        layer=Layer.OPTIC,
        prims=prims,
        description=f"Uncoated 8:92 pellicle (2 µm membrane) on a Ø{2 * spec.outer_radius:.2f}\" ring.",
    )


def make_bp108() -> PartSpec:
    return _make_pellicle(BP108, 'BP108 pellicle beamsplitter, Ø1", 8:92')


def make_bp208() -> PartSpec:
    return _make_pellicle(BP208, 'BP208 pellicle beamsplitter, Ø2", 8:92')


@dataclass(frozen=True)
class KinematicPellicleMount:
    """An L-shaped KM100BP-style mount, measured on its STEP file.

    Part frame: origin on the pellicle axis, at the depth of the 8-32 taps
    (mid back plate).  Both plates are L-brackets along the bottom and the +y
    side, so the region around the beam stays open.
    """

    part_id: str
    pellicle: PellicleSpec
    inset: float  # pellicle axis from the bottom and from the +y face
    tap_inset: float  # 8-32 taps from those faces
    size: float  # back plate, square
    back_x: tuple[float, float]
    front_x: tuple[float, float]
    front_arm: float
    seat_x: float  # the lip the ring sits on
    knobs: tuple[tuple[float, float], ...]  # (y, z) of the two adjusters
    knob_standoff: float = 0.0  # extra screw length behind the back plate before the knob

    @property
    def membrane_x(self) -> float:
        return self.seat_x + PELLICLE_FRAME_T


# KM100BP rev A + STEP: lip at x 0.293..0.357, ring recessed 0.03" below the face.
KM100BP_SPEC = KinematicPellicleMount(
    "KM100BP", BP108, inset=1.05, tap_inset=1.00, size=1.97, back_x=(-0.20, 0.20), front_x=(0.29, 0.572),
    front_arm=0.31, seat_x=0.357, knobs=((0.785, 0.70), (-0.70, -0.785)),
)
# KM200BP STEP: pellicle axis 1.605" from both faces, taps 0.045" closer; the
# 1/4"-80 adjusters stand 0.13" further back than on the KM100BP.
KM200BP_SPEC = KinematicPellicleMount(
    "KM200BP", BP208, inset=1.605, tap_inset=1.56, size=3.01, back_x=(-0.20, 0.17), front_x=(0.35, 0.74),
    front_arm=0.42, seat_x=0.405, knobs=((1.295, 1.155), (-1.155, -1.295)), knob_standoff=0.13,
)
# Kept for code that refers to the KM100BP's membrane plane.
KM100BP_FRONT_X = KM100BP_SPEC.front_x[1]
PELLICLE_PLANE_X = KM100BP_SPEC.membrane_x


def _make_kinematic_pellicle_mount(spec: KinematicPellicleMount, name: str) -> PartSpec:
    y_out, z_low = spec.inset, -spec.inset  # outer +y face and bottom face
    z_top = z_low + spec.size
    back_y = (y_out - spec.size, y_out)
    x0, x1 = spec.front_x
    front = _l_plate((x0, x1), (back_y[0] + 0.03, y_out - 0.05), (z_low, z_top - 0.03), spec.front_arm, "anodized")
    back_x = spec.back_x[0] - spec.knob_standoff
    prims = _kinematic_body(
        front=front, back_x=spec.back_x, back_y=back_y, plate_z=(z_low, z_top), front_x0=x0, knobs=()
    )
    for y, z in spec.knobs:
        if spec.knob_standoff:
            standoff = Cylinder("x", (spec.back_x[0] - spec.knob_standoff / 2, y, z), 0.125, spec.knob_standoff, "brass")
            prims.append(standoff)
        prims += _adjuster(y, z, back_x)
    taps = (
        MountingTap("bottom", "Upright (bottom hole)", (0.0, y_out - spec.tap_inset, z_low), (0, 0, -1)),
        MountingTap("side", "On its side (side hole, mirrored)", (0.0, y_out, z_low + spec.tap_inset), (0, 1, 0)),
    )
    size = spec.pellicle.diameter
    return PartSpec(
        id=spec.part_id,
        name=name,
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=taps,
        optic_slot=OpticSlot(seat_x=spec.membrane_x, loading="centered", diameter=size, clear_aperture=size),
        description=f'Kinematic mount for Ø{size:g}" pellicle beamsplitters ({spec.pellicle.part_id}).',
    )


def make_km100bp() -> PartSpec:
    return _make_kinematic_pellicle_mount(KM100BP_SPEC, 'KM100BP kinematic pellicle mount, Ø1"')


def make_km200bp() -> PartSpec:
    return _make_kinematic_pellicle_mount(KM200BP_SPEC, 'KM200BP kinematic pellicle mount, Ø2"')


@dataclass(frozen=True)
class PellicleCradle:
    """A BP107 / BP207 fixed pellicle mount, measured on its STEP file.

    A U-shaped cradle, 0.45" thick along the beam, open at the top.  The ring
    rests on a ledge and two front tabs clamp it, so the membrane sits 0.085"
    behind the cradle's front face.  Its 8-32 tap is in the bottom, under the
    pellicle, 0.165" behind the membrane.  Part frame: origin on the membrane.
    """

    part_id: str
    pellicle: PellicleSpec
    height: float  # pellicle axis above the bottom face
    inner_radius: float
    outer_radius: float
    arc_top: float  # the U's arms end this far above the axis

    front_x = 0.085
    back_x = -0.365
    tap_x = -0.165


BP107_SPEC = PellicleCradle("BP107", BP108, height=0.9492, inner_radius=0.715, outer_radius=0.843, arc_top=0.12)
BP207_SPEC = PellicleCradle("BP207", BP208, height=1.4512, inner_radius=1.213, outer_radius=1.340, arc_top=0.12)


def _make_pellicle_cradle(spec: PellicleCradle, name: str) -> PartSpec:
    x0, x1 = spec.back_x, spec.front_x
    r_in, r_out = spec.inner_radius, spec.outer_radius
    bottom = -r_in  # where the U's inner curve bottoms out
    prims: list[Primitive] = [
        # The two arms, straight up the sides (the curve is drawn by the CAD sprite).
        Box((x0, r_in, -0.7 * r_in), (x1, r_out, spec.arc_top), "anodized"),
        Box((x0, -r_out, -0.7 * r_in), (x1, -r_in, spec.arc_top), "anodized"),
        # The bottom of the U, narrowing toward the foot.
        Box((x0, -0.72 * r_out, bottom - 0.06), (x1, 0.72 * r_out, -0.7 * r_in), "anodized"),
        Box((x0, -0.3 * r_out, -spec.height), (x1, 0.3 * r_out, bottom - 0.06), "anodized"),
    ]
    taps = (MountingTap("bottom", "Upright (bottom tap)", (spec.tap_x, 0.0, -spec.height), (0, 0, -1)),)
    size = spec.pellicle.diameter
    return PartSpec(
        id=spec.part_id,
        name=name,
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=taps,
        optic_slot=OpticSlot(seat_x=0.0, loading="centered", diameter=size, clear_aperture=size),
        description=f'Fixed mount for Ø{size:g}" pellicle beamsplitters ({spec.pellicle.part_id}); 8-32 tap '
        "under the pellicle.",
    )


def make_bp107() -> PartSpec:
    return _make_pellicle_cradle(BP107_SPEC, 'BP107 fixed pellicle mount, Ø1"')


def make_bp207() -> PartSpec:
    return _make_pellicle_cradle(BP207_SPEC, 'BP207 fixed pellicle mount, Ø2"')


# Each pellicle mount and the ring it holds.
PELLICLE_MOUNTS: dict[str, PellicleSpec] = {
    "KM100BP": BP108, "KM200BP": BP208, "BP107": BP108, "BP207": BP208,
}


# ==========================================================================
# KMSR compact kinematic mount for 1"-tall rectangular optics (STEP)
# ==========================================================================
# Measured on the vendor STEP file: a 1.00"-wide back plate carrying the
# 8-32 tap (under the optic, mid-plate) and two hex adjusters, springs, then
# a "["-shaped holder whose top and bottom lips grip the 1"-tall optic
# against its solid back wall (a mirror mount: nothing passes through).  A
# nylon-tipped setscrew in the top lip holds the optic.  Its second 8-32 tap,
# in the back face on the optic axis, would point the mirror at the ceiling,
# so it is not offered.
KMSR_BACK = ((-0.140, 0.140), (-0.625, 0.500))  # back plate: x, z (1.00" wide)
KMSR_HOLDER = ((0.227, 0.427), (-0.590, 0.680))  # holder back wall: x, z
KMSR_LIPS = (((-0.590, -0.500), 0.673, 0.441), ((0.500, 0.680), 0.697, 0.416))  # (z range, front x, half width)
KMSR_KNOBS = ((0.340, 0.340), (-0.340, -0.340))  # (y, z) of the two hex adjusters
KMSR_KNOB_BACK = -0.281  # how far behind the post axis they reach
SQUARE_OPTIC_SIZE = 1.0  # PFSQ10-03-P01: 1" x 1" x 6 mm


def make_kmsr() -> PartSpec:
    (bx0, bx1), (bz0, bz1) = KMSR_BACK
    (hx0, hx1), (hz0, hz1) = KMSR_HOLDER
    prims: list[Primitive] = [
        Box((bx0, -0.5, bz0), (bx1, 0.5, bz1), "anodized_light"),
        Box((bx1 - 0.01, -0.30, -0.30), (hx0 + 0.01, 0.30, 0.30), "anodized"),  # springs and pivot, one body
        Box((hx0, -0.5, hz0), (hx1, 0.5, hz1), "anodized"),
    ]
    for (z0, z1), front, half_w in KMSR_LIPS:
        prims.append(Box((hx1, -half_w, z0), (front, half_w, z1), "anodized"))
    prims.append(Cylinder("z", (0.549, 0.0, KMSR_LIPS[1][0][1] + 0.02), 0.04, 0.04, "steel_dark"))  # optic setscrew
    for y, z in KMSR_KNOBS:
        prims.append(Cylinder("x", ((KMSR_KNOB_BACK + bx0) / 2, y, z), 0.121, bx0 - KMSR_KNOB_BACK, "steel_dark"))
    return PartSpec(
        id="KMSR",
        name='KMSR compact kinematic mount, 1" tall rectangular optics',
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=(MountingTap("bottom", "Upright (bottom tap)", (0.0, 0.0, bz0), (0, 0, -1)),),
        optic_slot=OpticSlot(seat_x=hx1, loading="front", diameter=SQUARE_OPTIC_SIZE, clear_aperture=0.90),
        description='Compact kinematic mount for 1"-tall rectangular optics over 3 mm thick; 8-32 tap under the optic.',
    )


def make_fmp1() -> PartSpec:
    """1.24 W x 1.57 H x 0.25 thick; optic 0.87" above the bottom; tap under the optic."""
    half_t = 0.125
    prims = (
        Box((-half_t, -0.62, -0.87), (half_t, 0.62, 0.70), "anodized"),
        Cylinder("z", (0.0, 0.0, 0.74), 0.07, 0.08, "steel_dark"),  # nylon-tipped setscrew
        Bore("x", (0.02, 0.0, 0.0), 0.50, 0.21),  # Ø1" seat, 0.21" deep from the front
        Bore("x", (-0.105, 0.0, 0.0), 0.47, 0.06),  # rear lip, Ø0.94 clear aperture
    )
    taps = (MountingTap("bottom", "Upright (bottom tap)", (0.0, 0.0, -0.87), (0, 0, -1)),)
    return PartSpec(
        id="FMP1",
        name='FMP1 fixed mount, Ø1"',
        layer=Layer.OPTIC,
        prims=prims,
        taps=taps,
        optic_slot=OpticSlot(seat_x=-0.085, loading="front", clear_aperture=0.94),
        description="Fixed mount for Ø1\" optics, 8-32 tap directly under the optic.",
    )


KB1X1_PLATE = 0.24  # each of its two plates
KB1X1_GAP = 0.02


def _kb1x1_bottom_plate() -> Box:
    top = -KB1X1_PLATE - KB1X1_GAP
    return Box((-0.5, -0.5, top - KB1X1_PLATE), (0.5, 0.5, top), "anodized")


def make_kb1x1() -> PartSpec:
    """1" x 1" magnetic kinematic base, 0.50" tall; origin at its top centre."""
    prims = (
        Box((-0.5, -0.5, -KB1X1_PLATE), (0.5, 0.5, 0.0), "anodized_light"),
        _kb1x1_bottom_plate(),
    )
    return PartSpec(
        id="KB1X1",
        name='KB1X1 magnetic kinematic base, 1" x 1"',
        layer=Layer.OPTIC,
        prims=prims,
        taps=(MountingTap("bottom", "Bottom", (0.0, 0.0, -2 * KB1X1_PLATE - KB1X1_GAP), (0, 0, -1)),),
        description="Magnetic kinematic base: remove and replace a mount repeatably.",
    )


def make_kb1x1_bottom() -> PartSpec:
    """The KB1X1's bottom plate alone: what stays on the post when the mount
    is lifted off with the top plate.  Same frame as the KB1X1."""
    return PartSpec(
        id="KB1X1-BOTTOM",
        name="KB1X1 bottom plate",
        layer=Layer.OPTIC,
        prims=(_kb1x1_bottom_plate(),),
        description="The half of a KB1X1 that stays on the post.",
    )


CAGE_SPACING = 30.0 / 25.4  # 30 mm cage rod spacing
CAGE_ROD_RADIUS = 3.0 / 25.4  # Ø6 mm rods
CP35_SIZE = 1.60
CP35_THICKNESS = 0.35


def make_cp35() -> PartSpec:
    """30 mm cage plate with a Ø1" double bore; origin at its front face, on axis."""
    half = CP35_SIZE / 2
    c = CAGE_SPACING / 2
    t = CP35_THICKNESS
    prims: list[Primitive] = [
        Box((-t, -half, -half), (0.0, half, half), "anodized"),
        Bore("x", (-t / 2, 0.0, 0.0), 0.50, t + 0.2),
        Cylinder("z", (-t / 2, 0.0, half + 0.03), 0.06, 0.06, "steel_dark"),  # optic setscrew
    ]
    for y in (-c, c):  # rod-locking setscrews on the top edge
        prims.append(Cylinder("z", (-t / 2, y, half + 0.025), 0.05, 0.05, "steel_dark"))
    taps = (MountingTap("bottom", "Bottom tap", (-t / 2, 0.0, -half), (0, 0, -1)),)
    return PartSpec(
        id="CP35",
        name="CP35 30 mm cage plate",
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=taps,
        optic_slot=OpticSlot(seat_x=-t / 2, loading="centered", clear_aperture=0.90),
        description="30 mm cage plate with a Ø1\" double bore and 8-32 tap.",
    )


# ==========================================================================
# Reflective objective in a cage plate: CP35 + M1RMSS + LMM40X-P01
# ==========================================================================
RMS_RADIUS = 0.400  # RMS objective thread, 0.800"-36
M1RMSS_LENGTH = 10.0 / 25.4  # STEP: a plain brass ring, 10 mm long
M1RMSS_RADII = (0.769 / 2, 1.031 / 2)  # STEP: RMS bore, SM1 outside


def make_m1rmss() -> PartSpec:
    """M1RMSS brass adapter: external SM1 thread (into the cage plate), internal
    RMS thread for the objective.  Origin: on the axis at its front face, where
    the objective's shoulder seats; +x toward the objective."""
    inner, outer = M1RMSS_RADII
    length = M1RMSS_LENGTH
    prims = (
        Cylinder("x", (-length / 2, 0.0, 0.0), outer, length, "brass"),
        Bore("x", (-length / 2, 0.0, 0.0), inner, length + 0.02),
    )
    return PartSpec(
        id="M1RMSS",
        name="M1RMSS brass adapter, external SM1 to internal RMS",
        layer=Layer.OPTIC,
        prims=prims,
        description="Brass microscope adapter ring: external SM1 threads, internal RMS threads, 10 mm long.",
    )


# LMM40X-P01: 40X reflective (two-mirror Schwarzschild) objective, protected
# silver, NA 0.50, infinity corrected.  Optics from the Thorlabs specs; the
# housing measured on the vendor STEP file, whose origin is the focus.
LMM40X_EFL_MM = 5.0
LMM40X_WD_MM = 7.8  # working distance: nose to focus
LMM40X_PUPIL_MM = 5.1  # entrance pupil diameter (= 2 NA f)
LMM40X_THREAD = 0.185  # RMS thread behind the shoulder
LMM40X_COLLAR = (0.047, 0.4626)  # collar in front of the shoulder: length, radius
LMM40X_BODY = (0.0866, 0.854, 0.5906)  # Ø30 mm housing: from, to, radius
LMM40X_NOSE = 0.874  # shoulder to nose (focus: + the working distance)
LMM40X_PATH_RADIUS = 0.14  # the light path through the housing, simplified to an axial bore


def make_lmm40x() -> PartSpec:
    """Origin: on the axis at the shoulder that seats on the adapter; +x toward
    the sample.  The RMS thread sticks out behind the shoulder."""
    collar, collar_r = LMM40X_COLLAR
    body0, body1, radius = LMM40X_BODY
    nose = LMM40X_NOSE
    prims = (
        Cylinder("x", (-LMM40X_THREAD / 2, 0.0, 0.0), RMS_RADIUS, LMM40X_THREAD, "anodized"),
        Cylinder("x", (collar / 2, 0.0, 0.0), collar_r, collar, "anodized"),
        Cylinder("x", ((collar + body0) / 2, 0.0, 0.0), (collar_r + radius) / 2, body0 - collar, "anodized"),
        Cylinder("x", ((body0 + body1) / 2, 0.0, 0.0), radius, body1 - body0, "anodized"),
        Cylinder("x", ((body1 + nose) / 2, 0.0, 0.0), radius - 0.02, nose - body1, "anodized"),
        Bore("x", (nose / 2, 0.0, 0.0), LMM40X_PATH_RADIUS, nose + 2 * LMM40X_THREAD),
    )
    return PartSpec(
        id="LMM40X",
        name="LMM40X-P01 40X reflective objective, NA 0.50",
        layer=Layer.OPTIC,
        prims=prims,
        description="Infinity-corrected 40X reflective objective, protected silver; EFL 5 mm, WD 7.8 mm, RMS thread.",
    )


SM1ZB_PLATE_HALF_T = 0.22  # 0.44" cage plate
SM1ZB_HOUSING_BACK = -0.58  # the 0.80"-deep translation housing behind the lower plate
SM1ZB_FIBER_X = 0.20  # estimate: fiber tip of an SM1-threaded adapter near the front face
SM1ZB_CARRIAGE_X = 0.32  # front of the SM1 carriage (sticks out 0.02-0.10")


def make_sm1zb() -> PartSpec:
    """SM1ZB z-axis translator for 30 mm cage systems (drawing rev B, 2025).

    Drawn as in the vendor drawing (housing and knob at the bottom): a 0.44"
    cage plate (1.60" wide, 2.12" across the knob side below the axis) with an
    SM1-threaded carriage (Ø0.90" clear aperture), a 0.80"-deep housing behind
    the lower half and the graduated adjuster knob.  Origin: optic axis at
    mid-plate.  Its only 8-32 hole is in the *top* edge, so on a post the part
    hangs inverted with the knob on top.
    """
    t = SM1ZB_PLATE_HALF_T
    prims: list[Primitive] = [
        Box((-t, -0.80, -0.45), (t, 0.80, 1.25), "anodized"),  # cage plate around the optic
        Box((-t, -1.32, -1.60), (t, 0.80, -0.45), "anodized"),  # lower plate, wider on the knob side
        Box((SM1ZB_HOUSING_BACK, -1.32, -1.58), (-t, 0.80, -0.40), "anodized_light"),  # translation housing
        Cylinder("x", (t + 0.05, 0.0, 0.0), 0.60, 0.10, "anodized_light"),  # SM1 carriage
        Cylinder("x", (-0.91, -1.02, -1.32), 0.26, 0.68, "steel_dark"),  # graduated barrel
        Cylinder("x", (-1.48, -1.02, -1.32), 0.345, 0.46, "knob", knurled=True),  # adjuster knob
        Bore("x", (0.0, 0.0, 0.0), 0.45, 1.2),  # Ø0.90" clear aperture
    ]
    taps = (
        MountingTap("top", "Housing toward the fiber (knob up)", (0.0, 0.0, 1.25), (0, 0, 1)),
        MountingTap("top-flipped", "Housing toward the lens (knob up)", (0.0, 0.0, 1.25), (0, 0, 1), yaw=180),
    )
    return PartSpec(
        id="SM1ZB",
        name="SM1ZB z-axis translator (30 mm cage)",
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=taps,
        description="SM1-threaded z-axis translator for 30 mm cage systems; holds the fiber adapter.",
    )


def make_fiber_connector() -> PartSpec:
    """FC/PC fiber adapter + connector boot sticking out of the back of the SM1ZB."""
    prims = (
        Cylinder("x", (-0.15, 0.0, 0.0), 0.20, 0.30, "steel"),
        Cylinder("x", (-0.55, 0.0, 0.0), 0.12, 0.50, "fiber"),
    )
    return PartSpec(id="FIBER", name="FC/PC fiber connector", layer=Layer.OPTIC, prims=prims)


def cage_rod_prims(length: float) -> tuple[Primitive, ...]:
    """A plain Ø6 mm ER cage rod of the given length along x, centred on its origin."""
    return (Cylinder("x", (0.0, 0.0, 0.0), CAGE_ROD_RADIUS, length, "steel"),)


def make_cage_rod() -> PartSpec:
    """ER cage rod; units build their own length with :func:`cage_rod_prims`."""
    return PartSpec(id="ER", name="ER cage rod (Ø6 mm)", layer=Layer.OPTIC, prims=cage_rod_prims(1.5))


# ==========================================================================
# BE02R 2X reflective beam expander (drawing rev B + STEP)
# ==========================================================================
# A housing (base flange, side walls, lid) holding two fixed spherical
# mirrors.  The beam enters through an SM05 port, meets a convex mirror
# (R 51.5 mm), then a concave one (R 100 mm, f = 50 mm), and leaves through
# a port in the 79° face.
#
# Part frame: origin where the input-port axis meets mirror 1, at beam
# height; +x along the input beam; z up.  (No post: the origin is also the
# unit origin.)  All numbers were measured from the STEP file, whose mirror
# surfaces are exact spheres.
BE02R_BOTTOM = -0.984  # the beam is 0.98" (25.0 mm) above the base
BE02R_FLANGE_TOP = -0.748
BE02R_WALL_TOP = 0.551
BE02R_LID_TOP = 0.650
BE02R_FLANGE = (
    (-1.535, 0.787), (0.558, 0.787), (0.588, 0.772), (1.003, -1.300), (0.990, -1.337),
    (-0.380, -1.613), (-1.535, -1.613), (-1.565, -1.599), (-1.574, -1.575), (-1.572, 0.759), (-1.557, 0.780),
)
BE02R_OUTLINE = (  # walls and lid; the edge from (0.465, 0.602) to (0.818, -1.181) is the 79° output face
    (-1.378, 0.629), (0.436, 0.628), (0.465, 0.602), (0.818, -1.181), (0.810, -1.213), (0.788, -1.226),
    (-0.371, -1.456), (-1.378, -1.456), (-1.402, -1.447), (-1.417, -1.417), (-1.417, 0.591), (-1.408, 0.615),
)
BE02R_CAVITY = (
    (-0.828, 0.508), (-0.899, 0.493), (-0.966, 0.465), (-1.063, 0.394), (-1.135, 0.296), (-1.174, 0.181),
    (-1.180, 0.110), (-1.180, -0.961), (-1.154, -1.079), (-1.109, -1.165), (-1.063, -1.220), (-1.007, -1.267),
    (-0.944, -1.302), (-0.875, -1.326), (-0.780, -1.338), (-0.423, -1.338), (-0.346, -1.330), (0.202, -1.221),
    (0.273, -1.195), (0.337, -1.156), (0.410, -1.088), (0.463, -1.003), (0.494, -0.908), (0.500, -0.814),
    (0.307, 0.188), (0.285, 0.260), (0.236, 0.347), (0.187, 0.403), (0.127, 0.449), (0.060, 0.483), (-0.037, 0.508),
)
BE02R_PORT_RADIUS = 0.256  # SM05 bores (Ø13 mm)
BE02R_OUTPUT_PORT = ((0.512, -0.374), 11.24)  # a point on its axis and its direction (square to the 79° face)
BE02R_SIDE_PLATE = ((-1.023, 0.631), (0.235, 0.902), -0.748, 0.472)  # 8-32 mounting plate: x/y corners, z range


@dataclass(frozen=True)
class FixedMirror:
    """A mirror built into a part, where the part's axial beam hits it (part frame)."""

    center: tuple[float, float]  # the axial beam's hit point
    normal_angle: float  # front-surface normal, degrees
    roc: float  # radius of curvature, inches: + concave, - convex
    aperture: tuple[float, float]  # extent along the tangent (normal turned +90°), relative to `center`
    thickness: float  # substrate behind the surface


def mirrors_along_axis(spheres, apertures, thicknesses) -> tuple[tuple[FixedMirror, ...], float]:
    """Follow the +x axis through the origin across spherical mirrors.

    ``spheres`` are ``((cx, cy), roc)`` in the part frame (roc < 0: convex).
    Returns the mirrors, located where the axis hits them, and the direction
    (degrees) of the beam that leaves the last one.
    """
    origin, direction = np.array([-10.0, 0.0]), np.array([1.0, 0.0])
    mirrors = []
    for ((cx, cy), roc), aperture, thickness in zip(spheres, apertures, thicknesses):
        centre, radius = np.array([cx, cy]), abs(roc)
        to_origin = origin - centre
        b = to_origin @ direction
        root = math.sqrt(b * b - (to_origin @ to_origin - radius * radius))
        # A convex mirror is hit on the near side of its sphere, a concave one from inside.
        hit = origin + (-b - root if roc < 0 else -b + root) * direction
        normal = (hit - centre) / radius if roc < 0 else (centre - hit) / radius  # facing the beam
        mirrors.append(
            FixedMirror(
                center=(float(hit[0]), float(hit[1])),
                normal_angle=math.degrees(math.atan2(normal[1], normal[0])),
                roc=roc,
                aperture=aperture,
                thickness=thickness,
            )
        )
        origin, direction = hit, direction - 2 * (direction @ normal) * normal
    return tuple(mirrors), math.degrees(math.atan2(direction[1], direction[0]))


_IN = 1 / 25.4
BE02R_MIRRORS, BE02R_EXIT_ANGLE = mirrors_along_axis(
    # The STEP's sphere centres (part frame, mm) and radii; the input axis meets mirror 1 at the origin.
    spheres=(
        ((47.866543 * _IN, 19.001159 * _IN), -51.5 * _IN),
        ((71.606675 * _IN, 27.620975 * _IN), 100.0 * _IN),
    ),
    apertures=((-0.2045, 0.0880), (-0.2660, 0.3808)),  # the mirror surfaces, measured on the STEP
    thicknesses=(0.092, 0.201),
)


def _be02r_walls(z0: float, z1: float, with_ports: bool) -> list[Primitive]:
    """Side walls between ``z0`` and ``z1``; at beam height the SM05 ports cut through them."""
    walls = Polygon(BE02R_OUTLINE, [BE02R_CAVITY])
    if with_ports:
        r = BE02R_PORT_RADIUS
        (ox, oy), angle = BE02R_OUTPUT_PORT
        output = affinity.rotate(box(-0.6, -r, 0.6, r), angle, origin=(0.0, 0.0))
        ports = [box(-1.8, -r, -0.9, r), affinity.translate(output, ox, oy)]
        walls = walls.difference(ports[0]).difference(ports[1])
    prims: list[Primitive] = []
    for piece in shapely.get_parts(walls):
        outline = tuple((round(x, 4), round(y, 4)) for x, y in piece.exterior.coords[:-1])
        holes = tuple(tuple((round(x, 4), round(y, 4)) for x, y in h.coords[:-1]) for h in piece.interiors)
        prims.append(Prism(outline, z0, z1, "anodized", holes))
    return prims


def _be02r_substrate(mirror: FixedMirror, half_height: float) -> Prism:
    """The glass behind a mirror surface, kept just behind its tangent line so
    the curved edges never shadow the surface itself."""
    angle = math.radians(mirror.normal_angle)
    n = np.array([math.cos(angle), math.sin(angle)])
    t = np.array([-n[1], n[0]])
    c = np.asarray(mirror.center)
    lo, hi = mirror.aperture
    corners = [c + u * t + w * n for u, w in ((lo, -0.003), (hi, -0.003), (hi, -mirror.thickness), (lo, -mirror.thickness))]
    return Prism(tuple((round(float(x), 4), round(float(y), 4)) for x, y in corners), -half_height, half_height, "steel")


def make_be02r() -> PartSpec:
    """Thorlabs BE02R 2X reflective beam expander, standing on its base flange."""
    port = BE02R_PORT_RADIUS
    (sx0, sy0), (sx1, sy1), sz0, sz1 = BE02R_SIDE_PLATE
    prims: list[Primitive] = [
        Prism(BE02R_FLANGE, BE02R_BOTTOM, BE02R_FLANGE_TOP, "anodized"),
        *_be02r_walls(BE02R_FLANGE_TOP, -port, with_ports=False),
        *_be02r_walls(-port, port, with_ports=True),
        *_be02r_walls(port, BE02R_WALL_TOP, with_ports=False),
        Prism(BE02R_OUTLINE, BE02R_WALL_TOP, BE02R_LID_TOP, "anodized"),
        Box((sx0, sy0, sz0), (sx1, sy1, sz1), "anodized"),
        _be02r_substrate(BE02R_MIRRORS[0], 0.146),
        _be02r_substrate(BE02R_MIRRORS[1], 0.335),
    ]
    return PartSpec(
        id="BE02R",
        name="BE02R 2X reflective beam expander",
        layer=Layer.OPTIC,
        prims=tuple(prims),
        description="Convex R 51.5 mm + concave R 100 mm mirrors in a housing with SM05 ports.",
    )


# ==========================================================================
# Base-layer parts (table level)
# ==========================================================================
PH2_BODY_R = 0.49  # Ø0.98" body, no flange
PH2_HEIGHT = 2.00
PH2_THUMBSCREW_Z = 1.50  # 0.50" below the top
BE1_R = 0.625  # Ø1.25"
BE1_HEIGHT = 0.185  # disc height (from the BE1 STEP; the 1/4"-20 stud hides inside the PH2)


def make_ph2(z_base: float = 0.0, part_id: str = "PH2") -> PartSpec:
    """Ø1/2" post holder, L = 2", with its TS25H thumbscrew pointing along +y."""
    top = z_base + PH2_HEIGHT
    screw_z = z_base + PH2_THUMBSCREW_Z
    r = PH2_BODY_R
    prims = (
        Cylinder("z", (0.0, 0.0, z_base + (PH2_HEIGHT - 0.06) / 2), r, PH2_HEIGHT - 0.06, "anodized"),
        Cylinder("z", (0.0, 0.0, top - 0.03), r - 0.08, 0.06, "anodized"),  # top chamfer
        Cylinder("z", (0.0, 0.0, top + 0.25), 0.25, 0.5, "steel"),  # TR post (hidden under the mount)
        # TS25H: thread stub, then the Ø0.57" knob 0.10..0.41" beyond the body.
        Cylinder("y", (0.0, r + 0.05, screw_z), 0.125, 0.12, "anodized"),
        Cylinder("y", (0.0, r + 0.255, screw_z), 0.285, 0.31, "knob", knurled=True),
    )
    return PartSpec(
        id=part_id,
        name='PH2 post holder, Ø1/2", L = 2"',
        layer=Layer.BASE,
        prims=prims,
        description="Ø1/2\" post holder (Ø0.98\" body) with spring-loaded hex-locking thumbscrew.",
    )


def make_be1() -> PartSpec:
    prims = (Cylinder("z", (0.0, 0.0, BE1_HEIGHT / 2), BE1_R, BE1_HEIGHT, "steel"),)
    return PartSpec(
        id="BE1",
        name='BE1 pedestal base adapter, Ø1.25"',
        layer=Layer.BASE,
        prims=prims,
        description="Ø1.25\" studded pedestal base adapter, 1/4\"-20 thread.",
    )


# CF125C clamping fork (drawing).  Pedestal centre at the origin, the fork
# running along +x; outline half-width vs. distance from the pedestal centre.
CF125C_PROFILE = (
    (-0.15, 0.67), (0.0, 0.715), (0.4, 0.65), (0.8, 0.56), (1.2, 0.48),
    (1.6, 0.435), (2.0, 0.435), (2.2, 0.45), (2.4, 0.45), (2.6, 0.35), (2.754, 0.0),
)
CF125C_OPENING_R = 0.505  # Ø1.01" opening straddling the post holder
CF125C_SCREW_TRAVEL = (1.054, 2.294)  # screw axis distance from the pedestal centre
CF125C_THROUGH_SLOT_W = 0.265
CF125C_COUNTERBORE_W = 0.53
CF125C_THICKNESS = 0.44
CF125C_FLOOR = 0.20  # estimate: counterbore floor height


def cf125c_screw_range() -> tuple[float, float]:
    """Min/max distance from the pedestal centre to the clamp screw axis."""
    return CF125C_SCREW_TRAVEL


def _stadium(x0: float, x1: float, half_w: float, segments: int = 8) -> tuple[tuple[float, float], ...]:
    """Slot outline: straight sides joining semicircular ends centred at x0 and x1."""
    pts = []
    for k in range(segments + 1):
        a = math.pi / 2 + math.pi * k / segments
        pts.append((x0 + half_w * math.cos(a), half_w * math.sin(a)))
    for k in range(segments + 1):
        a = -math.pi / 2 + math.pi * k / segments
        pts.append((x1 + half_w * math.cos(a), half_w * math.sin(a)))
    return tuple(pts)


def _fork_outline() -> tuple[tuple[float, float], ...]:
    """Fork outline: the drawing's width profile, open around the pedestal.

    Walk the upper edge tip -> heel, the lower edge back to the tip, then the
    Ø1.01" opening from the lower tine round to the upper tine.
    """
    radius = CF125C_OPENING_R
    tip_d = CF125C_PROFILE[0][0]
    a_tip = math.atan2(math.sqrt(radius**2 - tip_d**2), tip_d)
    upper = [(d, w) for d, w in CF125C_PROFILE]
    lower = [(d, -w) for d, w in reversed(CF125C_PROFILE[:-1])]
    opening = [(radius * math.cos(a), radius * math.sin(a)) for a in np.linspace(-a_tip, a_tip, 25)]
    return tuple((float(x), float(y)) for x, y in upper + lower + opening)


def make_cf125c() -> PartSpec:
    lo, hi = CF125C_SCREW_TRAVEL
    outline = _fork_outline()
    counterbore = _stadium(lo, hi, CF125C_COUNTERBORE_W / 2)
    through = _stadium(lo, hi, CF125C_THROUGH_SLOT_W / 2)
    prims = (
        Prism(outline, 0.0, CF125C_FLOOR, "steel", holes=(through,)),
        Prism(outline, CF125C_FLOOR, CF125C_THICKNESS, "steel", holes=(counterbore,)),
    )
    return PartSpec(
        id="CF125C",
        name="CF125C clamping fork",
        layer=Layer.BASE,
        prims=prims,
        description="Clamping fork, 1.24\" counterbored slot, captive 1/4\"-20 screw (2.91\" long).",
    )


def make_fork_screw() -> PartSpec:
    """1/4"-20 socket cap screw head (Ø0.375") sitting in the fork's counterbore."""
    z0 = CF125C_FLOOR
    prims = (
        Cylinder("z", (0.0, 0.0, z0 + 0.125), 0.1875, 0.25, "steel_dark"),
        Cylinder("z", (0.0, 0.0, z0 + 0.255), 0.07, 0.01, "knob"),  # hex socket
    )
    return PartSpec(id="SCREW", name='1/4"-20 cap screw', layer=Layer.BASE, prims=prims)


# ==========================================================================
# Enclosure framing: 1/2" T-slot extrusion and boards
# ==========================================================================
# A generic 1/2" x 1/2" four-slot extrusion (80/20's own "1/2"" profile,
# 1050, is 1" x 1/2"); the slot sizes are estimates.  Straight members run
# along +x from their origin, centred on the x axis, so a rail and the board
# in its slot share one line.  How high a member sits decides what it meets:
# beams only cross z = 0 (the beam plane).
TSLOT_SIZE = 0.5
TSLOT_OPENING = 0.13  # estimate: slot opening
TSLOT_NECK = 0.04  # estimate: depth of the opening, before the slot widens
TSLOT_CHAMBER = (0.22, 0.08)  # estimate: width and depth of the wider slot behind it
TSLOT_BORE = 0.06  # estimate: radius of the centre bore (tapped for end screws)
POST_Z = (-1.0, 1.5)  # a post (or board) reaches from below the beam plane to above it (drawn height)
BOARD_THICKNESS = 0.125  # 1/8" hardboard fits the slot


def tslot_profile() -> tuple[tuple[float, float], ...]:
    """End view of the extrusion: a square with a T-slot in the middle of each side."""
    h, half_open = TSLOT_SIZE / 2, TSLOT_OPENING / 2
    half_chamber, chamber_depth = TSLOT_CHAMBER[0] / 2, TSLOT_CHAMBER[1]
    neck, floor = -h + TSLOT_NECK, -h + TSLOT_NECK + chamber_depth
    side = [  # the bottom side (y = -h), walked toward +x; the other sides are this, turned
        (-h, -h), (-half_open, -h), (-half_open, neck), (-half_chamber, neck), (-half_chamber, floor),
        (half_chamber, floor), (half_chamber, neck), (half_open, neck), (half_open, -h),
    ]
    points = []
    for turn in range(4):
        c, s = round(math.cos(turn * math.pi / 2)), round(math.sin(turn * math.pi / 2))
        points += [(c * x - s * y, s * x + c * y) for x, y in side]
    return tuple((float(x), float(y)) for x, y in points)


def _circle(radius: float, segments: int = 16) -> tuple[tuple[float, float], ...]:
    return tuple(
        (radius * math.cos(2 * math.pi * k / segments), radius * math.sin(2 * math.pi * k / segments))
        for k in range(segments)
    )


def tslot_rail_prims(length: float, z_centre: float) -> tuple[Primitive, ...]:
    """A horizontal rail along x seen from above: its top face, with the slot
    running down the middle (lit lands either side, a shadowed groove)."""
    h, half_open = TSLOT_SIZE / 2, TSLOT_OPENING / 2
    top = z_centre + h
    floor = top - TSLOT_NECK - TSLOT_CHAMBER[1]
    return (
        Box((0.0, -h, z_centre - h), (length, h, floor), "aluminum"),
        Box((0.0, -h, floor), (length, -half_open, top), "aluminum"),
        Box((0.0, half_open, floor), (length, h, top), "aluminum"),
    )


def make_tslot_rail() -> PartSpec:
    """1/2" T-slot rail (shown 1" long; units build their own length)."""
    return PartSpec(
        id="TSLOT-RAIL",
        name='1/2" T-slot rail',
        layer=Layer.OPTIC,
        prims=tslot_rail_prims(1.0, 0.0),
        description='1/2" x 1/2" four-slot aluminium extrusion, lying horizontally.',
    )


def make_tslot_post() -> PartSpec:
    """1/2" T-slot post standing upright: seen end-on, origin on its axis."""
    prism = Prism(tslot_profile(), POST_Z[0], POST_Z[1], "aluminum", holes=(_circle(TSLOT_BORE),))
    return PartSpec(
        id="TSLOT-POST",
        name='1/2" T-slot post',
        layer=Layer.OPTIC,
        prims=(prism,),
        description='1/2" x 1/2" four-slot aluminium extrusion, standing upright.',
    )


def board_prims(length: float, thickness: float) -> tuple[Primitive, ...]:
    """An enclosure board standing on edge along x, centred on the x axis."""
    return (Box((0.0, -thickness / 2, POST_Z[0]), (length, thickness / 2, POST_Z[1]), "board"),)


def make_board() -> PartSpec:
    return PartSpec(
        id="BOARD",
        name="Enclosure board",
        layer=Layer.OPTIC,
        prims=board_prims(1.0, BOARD_THICKNESS),
        description="Opaque panel standing on edge, e.g. 1/8\" black hardboard in the T-slots.",
    )


# ==========================================================================
# MAX311D NanoMax 3-axis flexure stage (STEP)
# ==========================================================================
# Measured on the vendor STEP file (CAD mm, z up, the table at z = 0).  The
# base's counterbored holes sit on a 12.6 mm pitch made to fit both 1" and
# 25 mm grids.  The part origin is the 1" grid point they fit best, 0.8 mm
# from the base centre: with it on a breadboard hole, five of them land
# within 0.4 mm of holes.  x, y as in the file: the X drive points +x, the Y
# and Z drives +y; the 60 mm sample platform sits on the base's -x, -y corner.
MAX311D_ANCHOR_MM = (-59.3, 41.22)
MAX311D_PLATFORM_MM = ((-114.5, -54.5), (-13.98, 46.02), (58.5, 62.5))  # x, y, z
MAX311D_TRAVEL_MM = 4.0  # per axis (Thorlabs)


def _max311d_in(x_mm: float, y_mm: float) -> tuple[float, float]:
    """CAD mm -> part-frame inches."""
    ax, ay = MAX311D_ANCHOR_MM
    return (x_mm - ax) / 25.4, (y_mm - ay) / 25.4


def _max311d_box(x_mm, y_mm, z_mm, material: str) -> Box:
    (x0, y0), (x1, y1) = _max311d_in(x_mm[0], y_mm[0]), _max311d_in(x_mm[1], y_mm[1])
    return Box((x0, y0, z_mm[0] / 25.4), (x1, y1, z_mm[1] / 25.4), material)


def _max311d_drive(axis: str, across_mm: float, z_mm: float, start_mm: float, end_mm: float) -> list[Primitive]:
    """A differential micrometer along +axis: lock ring, barrel, then the thimble."""
    sections = ((start_mm, start_mm + 24.0, 12.5, "anodized"), (start_mm + 24.0, start_mm + 36.0, 7.5, "steel"),
                (start_mm + 36.0, end_mm, 10.3, "knob"))
    prims: list[Primitive] = []
    for a0, a1, radius_mm, material in sections:
        mid = (a0 + a1) / 2
        x, y = _max311d_in(mid, across_mm) if axis == "x" else _max311d_in(across_mm, mid)
        prims.append(Cylinder(axis, (x, y, z_mm / 25.4), radius_mm / 25.4, (a1 - a0) / 25.4, material,
                              knurled=material == "knob"))
    return prims


def max311d_face_centre(face: str) -> tuple[float, float]:
    """Centre of the platform face a sample faces out of (part frame, inches):
    ``"x"``: the -x face, away from the X drive; ``"y"``: the -y face."""
    (x0, x1), (y0, y1), _ = MAX311D_PLATFORM_MM
    return _max311d_in(x0, (y0 + y1) / 2) if face == "x" else _max311d_in((x0 + x1) / 2, y0)


def make_max311d() -> PartSpec:
    platform_x, platform_y, platform_z = MAX311D_PLATFORM_MM
    prims: list[Primitive] = [
        _max311d_box((-114.5, -2.5), (-13.98, 98.02), (0.0, 7.0), "anodized"),  # base plate
        _max311d_box((-114.5, -27.5), (-13.98, 73.0), (7.0, 55.5), "anodized"),  # flexure body
        _max311d_box((-107.5, -61.5), (-7.0, 39.0), (55.5, platform_z[0]), "anodized_light"),
        _max311d_box(platform_x, platform_y, platform_z, "anodized_light"),  # sample platform
        *_max311d_drive("y", -103.5, 30.0, 70.0, 147.17),
        *_max311d_drive("y", -58.0, 30.0, 70.0, 147.17),
        *_max311d_drive("x", 7.02, 42.0, -30.0, 43.65),
    ]
    return PartSpec(
        id="MAX311D",
        name="MAX311D NanoMax 3-axis flexure stage",
        layer=Layer.BASE,
        prims=tuple(prims),
        description="3-axis flexure stage, 4 mm travel per axis, differential drives and closed-loop piezos.",
    )


def sample_prims(width: float, thickness: float) -> tuple[Primitive, ...]:
    """A square sample standing at beam height; origin at its front surface, facing +x."""
    return (Box((-thickness, -width / 2, -width / 2), (0.0, width / 2, width / 2), "label"),)


def make_sample() -> PartSpec:
    return PartSpec(
        id="SAMPLE",
        name="Sample",
        layer=Layer.OPTIC,
        prims=sample_prims(1.0, 1.0 / 25.4),
        optic_slot=OpticSlot(seat_x=0.0, loading="rear", diameter=1.0, clear_aperture=1.0),
        description="A sample whose front surface faces +x.",
    )


# ==========================================================================
# MRAK25-P01 knife-edge prism mirror on a KM100B kinematic platform mount
# ==========================================================================
# A right-angle N-BK7 prism whose two 25 mm legs are silvered on the outside
# and meet in a bevel-free 90° edge.  Two beams arriving from opposite sides,
# square to the output, leave side by side as close together as their own
# sizes allow: the edge itself wastes no aperture.  Thorlabs shows it on a
# KM100B kinematic platform mount (1.92" square platform).  No vendor CAD is
# registered for either: the prism is drawn from its catalog dimensions, the
# KM100B is simplified and its thicknesses are estimates.
#
# Part frames: origin on the knife edge at beam height (the KM100B's tap is
# under it); +x is the direction the reflected beams leave, the way the edge
# points; the prism lies behind the edge, its legs at ±45°.
MRAK25_LEG = 25.0 / 25.4
MRAK25_HEIGHT = 25.0 / 25.4  # estimate: a 25 mm cube cut along its diagonal
KM100B_PLATFORM = 1.92
KM100B_PLATFORM_T = 0.25  # estimate
KM100B_GAP = 0.10  # estimate: springs between the platform and the fixed plate
KM100B_BASE_T = 0.35  # estimate


def mrak25_outline() -> tuple[tuple[float, float], ...]:
    """The prism seen from above (part frame): the knife edge, then the far ends of its two legs."""
    r = MRAK25_LEG / math.sqrt(2)
    return ((0.0, 0.0), (-r, r), (-r, -r))


def make_mrak25() -> PartSpec:
    half = MRAK25_HEIGHT / 2
    return PartSpec(
        id="MRAK25",
        name="MRAK25-P01 knife-edge right-angle prism mirror, 25 mm",
        layer=Layer.OPTIC,
        prims=(Prism(mrak25_outline(), -half, half, "glass"),),
        description="N-BK7 right-angle prism, protected silver on both 25 mm legs, bevel-free 90° knife edge.",
    )


def make_km100b() -> PartSpec:
    """Kinematic platform: a square platform on springs over a fixed plate with
    the 8-32 tap, tipped and tilted by two 1/4"-80 adjusters at the back."""
    half = KM100B_PLATFORM / 2
    top = -MRAK25_HEIGHT / 2  # the prism stands on the platform
    platform_bottom = top - KM100B_PLATFORM_T
    base_top = platform_bottom - KM100B_GAP
    base_bottom = base_top - KM100B_BASE_T
    knob_z = (base_top + base_bottom) / 2
    prims: list[Primitive] = [
        Box((-half, -half, platform_bottom), (half, half, top), "anodized"),
        Box((-0.30, -0.30, base_top - 0.01), (0.30, 0.30, platform_bottom + 0.01), "anodized"),  # springs, pivot
        Box((-half, -half, base_bottom), (half, half, base_top), "anodized_light"),
    ]
    for y in (0.62, -0.62):
        prims += _adjuster(y, knob_z, -half)
    return PartSpec(
        id="KM100B",
        name='KM100B kinematic platform mount, 1.92" x 1.92"',
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=(MountingTap("bottom", "Upright (bottom tap)", (0.0, 0.0, base_bottom), (0, 0, -1)),),
        description="Kinematic platform mount for prisms and rectangular optics, ±4° tip and tilt, 8-32 tap.",
    )


# ==========================================================================
# ID25 iris diaphragm (catalog dimensions; no vendor CAD registered)
# ==========================================================================
# Part frame: origin at the centre of the opening, at beam height; the beam
# runs along x through the housing, which stands in the y-z plane.
ID25_RADIUS = 43.69 / 25.4 / 2  # Ø43.7 mm housing
ID25_THICKNESS = 6.6 / 25.4
ID25_APERTURE_MM = (1.0, 25.0)  # the blades close from Ø25 mm down to Ø1 mm
ID25_OPENING = ID25_APERTURE_MM[1] / 25.4 / 2  # radius of the housing's own bore


def make_id25() -> PartSpec:
    r, t = ID25_RADIUS, ID25_THICKNESS
    prims = (
        Cylinder("x", (0.0, 0.0, 0.0), r, t, "anodized"),
        Bore("x", (0.0, 0.0, 0.0), ID25_OPENING, t + 0.1),
        Cylinder("z", (0.0, 0.42, r - 0.05), 0.05, 0.30, "steel_dark"),  # the lever, on the rim
    )
    return PartSpec(
        id="ID25",
        name="ID25 iris diaphragm, Ø1–25 mm",
        layer=Layer.OPTIC,
        prims=prims,
        taps=(MountingTap("bottom", "Upright (bottom tap)", (0.0, 0.0, -r), (0, 0, -1)),),
        description="Mounted iris, Ø1 mm to Ø25 mm aperture, 8-32 tap for a Ø1/2\" post.",
    )


# ==========================================================================
# Custom machined parts (drawings in docs/custom-parts.md)
# ==========================================================================
# BE02R riser: an L-shaped aluminium block that puts the BE02R's axis at beam
# height and fixes its position.  The BE02R stands on the riser and bolts
# through the four 8-32 holes of its side plate to the riser's wall; the riser
# bolts to the table with two 1/4"-20 screws in counterbores under the BE02R.
# Frame: the BE02R's (origin where the input axis meets mirror 1, at beam
# height; x along the input beam).  With mirror 1 on a whole-inch x and the
# input on a hole row, both bolts land on table holes.
RISER_WALL_Y = (0.9055, 1.2805)  # the wall the side plate bolts to (its face, then 0.375" of aluminium)
RISER_WALL_X = (-1.35, 0.55)
RISER_WALL_TOP = 0.616  # 1.600" above the riser's top face
RISER_SIDE_HOLES = ((-0.894, -0.646), (0.106, -0.646), (-0.894, 0.354), (0.106, 0.354))  # 8-32 in the side plate (x, z)
RISER_OUTLINE = ((-1.65, -1.7195), (1.10, -1.7195), (1.10, 1.2805), (-1.65, 1.2805))  # 2.750" x 3.000"
RISER_BOLTS = ((-0.5, -1.0), (0.5, -1.0))  # 1/4"-20 counterbored, under the BE02R
RISER_NOMINAL_H = 2.0  # drawn height; the real one is h - 25.0 mm (h = beam height above the table)


def make_be02r_riser() -> PartSpec:
    return PartSpec(
        id="BE02R-RISER",
        name="BE02R riser (custom)",
        layer=Layer.BASE,
        prims=(Prism(RISER_OUTLINE, 0.0, RISER_NOMINAL_H, "anodized_light"),),
        description="Machined riser: puts the BE02R's axis at beam height and registers it on two 1/4\"-20 holes.",
    )


def make_be02r_riser_wall() -> PartSpec:
    (x0, x1), (y0, y1) = RISER_WALL_X, RISER_WALL_Y
    return PartSpec(
        id="BE02R-RISER-WALL",
        name="BE02R riser wall (custom)",
        layer=Layer.OPTIC,
        prims=(Box((x0, y0, BE02R_BOTTOM), (x1, y1, RISER_WALL_TOP), "anodized_light"),),
        description="The riser's wall: the BE02R's side plate bolts to it with four 8-32 screws.",
    )


# Knife-edge holder: the MRAK25's hypotenuse face rests against a fence and a
# clamp plate holds its top; its edge is KNIFE_EDGE_LEAD ahead of the post axis.
# Two beams fed along the post's column (one from each side) then meet the
# legs 2 x KNIFE_EDGE_LEAD apart and leave side by side, 9.0 mm apart.
KNIFE_EDGE_LEAD = 4.5 / 25.4
KEH_FENCE_X = KNIFE_EDGE_LEAD - MRAK25_LEG / math.sqrt(2)  # the fence face = the prism's hypotenuse, behind the post
KEH_BASE_T = 0.375
KEH_BASE_LENGTH = 1.000  # from the fence's back to the base's front, past the edge
KEH_FENCE_T = 0.25
KEH_FENCE_CLEARANCE = 0.031  # the fence stands this much above the prism; the setscrew reaches down to it
KEH_HALF_WIDTH = 0.55  # the prism's back corners overhang the fence a little
KEH_CLAMP = (0.47, 0.80, 0.125)  # clamp plate on the fence: length from the fence's back, width, thickness
KEH_SETSCREW_X = 0.12  # the nylon-tipped setscrew, this far in front of the fence face


def make_knife_edge_holder() -> PartSpec:
    """Frame: origin on the post axis at beam height; +x is where the beams leave."""
    half, w, back = MRAK25_HEIGHT / 2, KEH_HALF_WIDTH, KEH_FENCE_X
    rear = back - KEH_FENCE_T
    bottom = -half - KEH_BASE_T
    top = half + KEH_FENCE_CLEARANCE
    clamp_length, clamp_width, clamp_t = KEH_CLAMP
    prims: list[Primitive] = [
        Box((rear, -w, bottom), (rear + KEH_BASE_LENGTH, w, -half), "anodized"),  # base, under the prism
        Box((rear, -w, -half), (back, w, top), "anodized"),  # fence behind the hypotenuse
        Box((rear, -clamp_width / 2, top), (rear + clamp_length, clamp_width / 2, top + clamp_t), "anodized_light"),
        Cylinder("z", (back + KEH_SETSCREW_X, 0.0, top + clamp_t / 2), 0.07, 0.08, "steel_dark"),  # setscrew
    ]
    return PartSpec(
        id="KEH",
        name="Knife-edge holder for MRAK25 (custom)",
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=(MountingTap("bottom", "Upright (bottom tap)", (0.0, 0.0, bottom), (0, 0, -1)),),
        description="Machined holder: fence + clamp plate; the knife edge 4.5 mm ahead of the 8-32 post tap.",
    )


# Beam block (custom): a black-anodized aluminium block on an 8-32 post, for
# milliwatt strays where an LB1 does not fit.  3/8" thick so its bottom edge
# takes the 8-32 tap.  Turned ~20° from square so its weak specular
# reflection goes into the table.  Frame: origin on the post axis, the face
# toward +x.
BEAM_BLOCK = ((-0.1875, -0.50, -0.60), (0.1875, 0.50, 0.40))


def make_beam_block() -> PartSpec:
    lo, hi = BEAM_BLOCK
    return PartSpec(
        id="BLOCK",
        name="Beam block plate (custom)",
        layer=Layer.OPTIC,
        prims=(Box(lo, hi, "anodized"),),
        taps=(MountingTap("bottom", "Upright (bottom tap)", (0.0, 0.0, lo[2]), (0, 0, -1)),),
        description='Black-anodized block, 1.0" x 1.0" x 3/8", 8-32 tap in its bottom edge.',
    )


# Alignment target (custom): a 3/8"-thick aluminium plate, 1.6" wide, on an
# 8-32 post, with one or two small pinholes drilled through a thin web left by
# counterbores from the back.  Two pinholes check two beams that run side by
# side (e.g. after the knife edge).  Frame: origin on the post axis at beam
# height; the beams cross the plate along x.
TARGET_PLATE = ((-0.1875, -0.80, -0.60), (0.1875, 0.80, 0.50))


def make_target_plate() -> PartSpec:
    lo, hi = TARGET_PLATE
    return PartSpec(
        id="TARGET",
        name="Pinhole alignment target (custom)",
        layer=Layer.OPTIC,
        prims=(Box(lo, hi, "anodized"),),
        taps=(MountingTap("bottom", "Upright (bottom tap)", (0.0, 0.0, lo[2]), (0, 0, -1)),),
        description='Aluminium plate, 1.6" x 1.1" x 3/8", with one or two pinholes on the beam line.',
    )


# Beam dump: Thorlabs LB1 (1.4" x 0.7" aperture, 10 W CW) on its Ø1/2" post.
# Its outer dimensions are estimates.  Frame: origin on the post axis, the
# aperture facing +x (toward the beam).
LB1_BODY = ((-1.00, -0.95, -0.70), (0.10, 0.95, 0.70))  # estimate


def make_lb1() -> PartSpec:
    lo, hi = LB1_BODY
    return PartSpec(
        id="LB1",
        name="LB1 beam dump (outer dimensions estimated)",
        layer=Layer.OPTIC,
        prims=(Box(lo, hi, "anodized"),),
        taps=(MountingTap("bottom", "Upright (bottom tap)", (0.0, 0.0, lo[2]), (0, 0, -1)),),
        description='Beam dump, 1.4" x 0.7" aperture, 10 W CW, on a Ø1/2" post.',
    )


# ==========================================================================
# Registry
# ==========================================================================
def _build_registry() -> dict[str, PartSpec]:
    parts = [
        make_km100(),
        make_km100cp(),
        make_km100bp(),
        make_km200bp(),
        make_bp107(),
        make_bp207(),
        make_bp108(),
        make_bp208(),
        make_kmsr(),
        make_fmp1(),
        make_kb1x1(),
        make_kb1x1_bottom(),
        make_cp35(),
        make_m1rmss(),
        make_lmm40x(),
        make_sm1zb(),
        make_fiber_connector(),
        make_cage_rod(),
        make_be02r(),
        make_ph2(),
        make_ph2(z_base=BE1_HEIGHT, part_id="PH2@BE1"),  # PH2 standing on a BE1
        make_be1(),
        make_cf125c(),
        make_fork_screw(),
        make_tslot_rail(),
        make_tslot_post(),
        make_board(),
        make_max311d(),
        make_sample(),
        make_mrak25(),
        make_km100b(),
        make_id25(),
        make_be02r_riser(),
        make_be02r_riser_wall(),
        make_knife_edge_holder(),
        make_lb1(),
        make_beam_block(),
        make_target_plate(),
    ]
    return {part.id: part for part in parts}


PARTS: dict[str, PartSpec] = _build_registry()


def get_part(part_id: str) -> PartSpec:
    try:
        return PARTS[part_id]
    except KeyError as exc:
        raise KeyError(f"unknown part {part_id!r}") from exc
