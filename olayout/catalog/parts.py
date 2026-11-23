"""Individual Thorlabs parts, modelled with simple 3D primitives.

Part frames
-----------
* Optic-layer parts (mounts, cage plates, kinematic bases): origin on the
  optic axis, directly above the primary mounting tap; +x points out of the
  front face (toward the light hitting a mirror); +y is your right when you
  face the optic; z up.  The beam plane is z = 0.
* Base-layer parts (post holders, pedestal adapters, clamping forks): origin
  on the table surface at the post axis, z up.

Dimensions are inches from archived Thorlabs mechanical drawings (KM100
rev C, KM100CP, KM100BP, FMP1/M, CP33/CP35, PH1/PH4, TS25H, CF125C, TR2,
MB1224).  Hidden internals are simplified; values not on a drawing are
marked "estimate".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np

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
    """A tapped (or #8 clearance) hole a post or adapter attaches to."""

    name: str
    label: str
    position: Vec3  # centre of the hole on the part's face (part frame)
    normal: Vec3  # outward normal of that face, axis-aligned


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
        """Roll the part about its optic axis so ``tap_name`` faces the post."""
        tap = self.tap(tap_name)
        rot = roll_bringing_down(tap.normal)
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
KM_BACK_BORE_R = 0.35  # estimate: through-hole in the back plate


def _adjuster(y: float, z: float, x_back: float) -> list[Primitive]:
    """1/4"-80 adjuster sticking out 0.68" behind the back plate (toward -x):
    Ø0.25 screw for 0.23", a Ø0.46 hub, then the Ø0.58 knurled knob."""
    return [
        Cylinder("x", (x_back - 0.115, y, z), 0.125, 0.23, "brass"),
        Cylinder("x", (x_back - 0.29, y, z), 0.23, 0.12, "knob"),
        Cylinder("x", (x_back - 0.515, y, z), 0.29, 0.33, "knob", knurled=True),
    ]


def _kinematic_body(
    *, front_x: tuple[float, float], back_x: tuple[float, float], dy: float = 0.0, dz: float = 0.0
) -> list[Primitive]:
    """Front plate, back plate, linking web and adjusters (bores added by callers).

    ``dy, dz`` shift the plates relative to the optic centre (KM100BP).
    """
    fy = (KM_PLATE_Y[0] + dy, KM_PLATE_Y[1] + dy)
    by = (KM_BACK_Y[0] + dy, KM_BACK_Y[1] + dy)
    pz = (KM_PLATE_Z[0] + dz, KM_PLATE_Z[1] + dz)
    prims: list[Primitive] = [
        Box((front_x[0], fy[0], pz[0]), (front_x[1], fy[1], pz[1]), "anodized"),
        Box((back_x[0], by[0], pz[0]), (back_x[1], by[1], pz[1]), "anodized_light"),
        # Springs and the pivot ball live in the gap; a low web keeps it one body.
        Box((back_x[1] - 0.01, fy[0] + 0.12, pz[0] + 0.1), (front_x[0] + 0.01, fy[1] - 0.12, pz[0] + 0.25), "anodized"),
    ]
    for y, z in KM_KNOBS:
        prims += _adjuster(y + dy, z + dz, back_x[0])
    return prims


def make_km100() -> PartSpec:
    # Rev C: main face at x=0.58 (pads 0.63), front plate back at 0.30, 0.10"
    # gap, back plate -0.20..0.20; the #8 holes are mid back plate.
    prims = _kinematic_body(front_x=(0.30, 0.58), back_x=(-0.20, 0.20))
    prims += [
        Bore("x", (0.46, 0.0, 0.0), 0.50, 0.30),  # Ø1" seat, 0.245" deep
        Bore("x", (0.31, 0.0, 0.0), 0.47, 0.07),  # rear lip, Ø0.94 clear aperture
        Bore("x", (0.0, 0.0, 0.0), KM_BACK_BORE_R, 0.5),
    ]
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
    """
    prims = _kinematic_body(front_x=(-0.20, 0.08), back_x=(-0.70, -0.30))
    prims += [
        Box((-0.70, -0.375, -1.25), (0.20, 0.375, KM_PLATE_Z[0]), "anodized_light"),  # foot
        Box((-0.45, 1.00, -1.25), (0.20, 1.25, 0.30), "anodized_light"),  # side ear (height estimate)
        Bore("x", (-0.10, 0.0, 0.0), 0.50, 0.22),  # Ø1" cell, 0.20" deep
        Bore("x", (0.04, 0.0, 0.0), 0.47, 0.12),  # front lip, Ø0.94 clear aperture
        Bore("x", (-0.50, 0.0, 0.0), KM_BACK_BORE_R, 0.5),
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


PELLICLE_PLANE_X = 0.76  # estimate: membrane on the front of the BP1xx frame


KM100BP_ARM = 0.43  # width of the L-shaped plates' arms (estimate from the drawing)


def make_km100bp() -> PartSpec:
    """L-shaped kinematic mount for BP1xx pellicles (KM100BP drawing, rev A).

    Both plates are L-brackets along the +y side and the bottom, so the
    region around the beam is open and a 45° transmitted beam passes.  The
    pellicle centre is 1.05" from the bottom and from the +y face; the #8
    holes are 1.00" from those faces.  Plate depths match the KM100.
    """
    y_out, z_low = 1.05, -1.05  # outer +y face and bottom face
    y_in, z_top = y_out - 0.05 - 1.97, z_low + 1.97  # far ends of the arms
    arm = KM100BP_ARM
    prims: list[Primitive] = []
    for (x0, x1), material in (((0.30, 0.63), "anodized"), ((-0.20, 0.20), "anodized_light")):
        prims.append(Box((x0, y_out - arm, z_low), (x1, y_out, z_top), material))  # vertical arm (+y side)
        prims.append(Box((x0, y_in, z_low), (x1, y_out, z_low + arm), material))  # bottom arm
    # Web between the plates (springs / pivot) at the corner of the L.
    prims.append(Box((0.19, y_out - arm + 0.05, z_low + 0.05), (0.31, y_out - 0.05, z_low + arm - 0.05), "anodized"))
    for y, z in ((0.785, 0.70), (-0.70, -0.785)):  # adjusters at the ends of the arms
        prims += _adjuster(y, z, -0.20)
    prims += [
        Cylinder("x", (0.70, 0.0, 0.0), 0.69, 0.14, "anodized"),  # BP1xx frame (Ø1.38") on the front face
        Bore("x", (0.70, 0.0, 0.0), 0.50, 0.30),
    ]
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
        optic_slot=OpticSlot(seat_x=PELLICLE_PLANE_X, loading="centered", clear_aperture=0.90),
        description="Kinematic mount for Ø1\" BP1xx pellicle beamsplitters.",
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


def make_kb1x1() -> PartSpec:
    """1" x 1" magnetic kinematic base, 0.50" tall; origin at its top centre."""
    plate, gap = 0.24, 0.02
    prims = (
        Box((-0.5, -0.5, -plate), (0.5, 0.5, 0.0), "anodized_light"),
        Box((-0.5, -0.5, -2 * plate - gap), (0.5, 0.5, -plate - gap), "anodized"),
    )
    return PartSpec(
        id="KB1X1",
        name='KB1X1 magnetic kinematic base, 1" x 1"',
        layer=Layer.OPTIC,
        prims=prims,
        taps=(MountingTap("bottom", "Bottom", (0.0, 0.0, -2 * plate - gap), (0, 0, -1)),),
        description="Magnetic kinematic base: remove and replace a mount repeatably.",
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


def make_sm1zb() -> PartSpec:
    """SM1 z-axis translator for 30 mm cage systems, post-mountable (approximate).

    No archived Thorlabs drawing exists for "SM1ZB"; this follows the SM1ZA
    (cage-compatible) with SM1ZP-style post taps.  Origin: optic axis at the
    front (cage-rod) face.
    """
    size, body_t = 1.60, 0.50
    half = size / 2
    ring_r, ring_t = 0.80, 0.30
    prims: list[Primitive] = [
        Box((-body_t, -half, -half), (0.0, half, half), "anodized"),
        Cylinder("x", (-body_t - ring_t / 2, 0.0, 0.0), ring_r, ring_t, "anodized_light", knurled=True),
        Cylinder("x", (-body_t - ring_t - 0.20, 0.0, 0.0), 0.56, 0.40, "anodized"),  # SM1 tube
        Bore("x", (-0.5, 0.0, 0.0), 0.45, 1.6),
    ]
    taps = (
        MountingTap("bottom", "Upright (bottom tap)", (-body_t / 2, 0.0, -half), (0, 0, -1)),
        MountingTap("side", "On its side (side tap)", (-body_t / 2, half, 0.0), (0, 1, 0)),
        MountingTap("top", "Upside down (top tap)", (-body_t / 2, 0.0, half), (0, 0, 1)),
    )
    return PartSpec(
        id="SM1ZB",
        name="SM1ZB z-axis translation mount (30 mm cage)",
        layer=Layer.OPTIC,
        prims=tuple(prims),
        taps=taps,
        description="SM1 z-translation mount for 30 mm cage systems (approximate model); holds the fiber adapter.",
    )


def make_fiber_connector() -> PartSpec:
    """FC/PC fiber adapter + connector boot sticking out of the back of the SM1ZB."""
    prims = (
        Cylinder("x", (-0.15, 0.0, 0.0), 0.20, 0.30, "steel"),
        Cylinder("x", (-0.55, 0.0, 0.0), 0.12, 0.50, "fiber"),
    )
    return PartSpec(id="FIBER", name="FC/PC fiber connector", layer=Layer.OPTIC, prims=prims)


def make_cage_rod() -> PartSpec:
    """Four Ø6 mm cage rods, 1" long (stretched along x when drawn)."""
    c = CAGE_SPACING / 2
    prims = tuple(Cylinder("x", (-0.5, y, z), CAGE_ROD_RADIUS, 1.0, "steel") for y in (-c, c) for z in (-c, c))
    return PartSpec(id="ER", name="ER cage rods (30 mm)", layer=Layer.OPTIC, prims=prims)


# ==========================================================================
# Base-layer parts (table level)
# ==========================================================================
PH2_BODY_R = 0.49  # Ø0.98" body, no flange
PH2_HEIGHT = 2.00
PH2_THUMBSCREW_Z = 1.50  # 0.50" below the top
BE1_R = 0.625  # Ø1.25"
BE1_HEIGHT = 0.19  # estimate (not on a drawing)


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
        make_fmp1(),
        make_kb1x1(),
        make_cp35(),
        make_sm1zb(),
        make_fiber_connector(),
        make_cage_rod(),
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
