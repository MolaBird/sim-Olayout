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


KM100BP_FRONT_X = 0.58  # front face of the KM100BP, where the pellicle frame mounts
BP108_FRAME_T = 0.19  # estimate: BP108 frame thickness (Thorlabs gives OD Ø1.38", membrane Ø1")
PELLICLE_PLANE_X = KM100BP_FRONT_X + BP108_FRAME_T  # the membrane, where the beam hits


def make_km100bp() -> PartSpec:
    """L-shaped kinematic mount for Ø1" pellicles (KM100BP drawing rev A + STEP).

    Both plates are L-brackets along the +y side and the bottom (front arms
    0.31" wide, back arms 0.48"), so the region around the beam is open.  The
    pellicle centre is 1.05" from the bottom and from the +y face; the #8
    holes are 1.00" from those faces.  The pellicle itself is the separate
    BP108 part (its frame screws to the front face).
    """
    y_out, z_low = 1.05, -1.05  # back plate's outer +y face and bottom face
    z_top = z_low + 1.97
    back_y = (y_out - 1.97, y_out)
    front = _l_plate((0.29, KM100BP_FRONT_X), (back_y[0] + 0.03, y_out - 0.05), (z_low, z_top - 0.03), 0.31, "anodized")
    knobs = ((0.785, 0.70), (-0.70, -0.785))  # at the ends of the arms
    prims = _kinematic_body(
        front=front, back_x=(-0.20, 0.20), back_y=back_y, plate_z=(z_low, z_top), front_x0=0.29, knobs=knobs
    )
    taps = (
        MountingTap("bottom", "Upright (bottom hole)", (0.0, y_out - 1.00, z_low), (0, 0, -1)),
        MountingTap("side", "On its side (side hole, mirrored)", (0.0, y_out, z_low + 1.00), (0, 1, 0)),
    )
    return PartSpec(
        id="KM100BP",
        name='KM100BP kinematic pellicle mount, Ø1"',
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=taps,
        optic_slot=OpticSlot(seat_x=PELLICLE_PLANE_X, loading="centered", clear_aperture=1.00),
        description="Kinematic mount for Ø1\" pellicle beamsplitters (BP108).",
    )


BP108_REFLECTANCE = 0.08  # 8:92 uncoated pellicle, 400-2400 nm


def make_bp108() -> PartSpec:
    """BP108 pellicle beamsplitter (8:92): 2 µm membrane on a Ø1.38" frame.

    Origin = membrane centre, on the frame's front face; the frame extends
    behind it (toward -x) onto the KM100BP.  The beam is normally incident on
    the membrane side.
    """
    t = BP108_FRAME_T
    prims = (
        Cylinder("x", (-t / 2, 0.0, 0.0), 0.69, t, "anodized"),
        Bore("x", (-t / 2, 0.0, 0.0), 0.50, t + 0.1),  # Ø1" membrane
    )
    return PartSpec(
        id="BP108",
        name='BP108 pellicle beamsplitter, Ø1", 8:92',
        layer=Layer.OPTIC,
        prims=prims,
        description="Uncoated 8:92 pellicle (2 µm membrane) on a Ø1.38\" frame.",
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
# Registry
# ==========================================================================
def _build_registry() -> dict[str, PartSpec]:
    parts = [
        make_km100(),
        make_km100cp(),
        make_km100bp(),
        make_bp108(),
        make_fmp1(),
        make_kb1x1(),
        make_kb1x1_bottom(),
        make_cp35(),
        make_sm1zb(),
        make_fiber_connector(),
        make_cage_rod(),
        make_be02r(),
        make_ph2(),
        make_ph2(z_base=BE1_HEIGHT, part_id="PH2@BE1"),  # PH2 standing on a BE1
        make_be1(),
        make_cf125c(),
        make_fork_screw(),
    ]
    return {part.id: part for part in parts}


PARTS: dict[str, PartSpec] = _build_registry()


def get_part(part_id: str) -> PartSpec:
    try:
        return PARTS[part_id]
    except KeyError as exc:
        raise KeyError(f"unknown part {part_id!r}") from exc
