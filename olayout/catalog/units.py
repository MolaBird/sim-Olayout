"""Combined units: what the user actually places on the breadboard.

Each :class:`UnitType` knows its bill of materials, how it is fastened to
the table (:class:`Mounting`), its editable parameters (:class:`ParamField`)
and how to *build* itself: a :class:`UnitBuild` listing the placed parts,
optical elements, emitters and beam blockers in the unit frame.

Unit frame: origin on the post axis (table level), +x is the direction the
optic's front face points (for sources: the beam direction).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import Any, Callable

import numpy as np
import shapely
from shapely.geometry import Polygon, box

from olayout.catalog import parts as P
from olayout.catalog.parts import Layer, PartSpec, get_part
from olayout.catalog.primitives import (
    Primitive,
    bounds3d,
    footprint,
    slice_at,
    transform_all,
)
from olayout.core.geometry import Pose, as_polygonal, transform_geometry
from olayout.core.layout import Component
from olayout.core.units import MM, NM, UM
from olayout.optics.elements import Blocker, Emitter, OpticalElement, OpticKind


class Mounting(str, Enum):
    HOLE = "hole"  # post holder screwed straight into a hole: snaps to the hole grid
    PEDESTAL = "pedestal"  # PH2 + BE1 + CF125C: anywhere; the fork screw needs a hole in reach
    FREE = "free"  # virtual objects (e.g. a beam source): no mechanical constraints


# ==========================================================================
# Parameters
# ==========================================================================
@dataclass(frozen=True)
class ParamField:
    """One editable parameter, stored at a dotted ``key`` inside ``params``."""

    key: str
    label: str
    kind: str  # "float" | "int" | "bool" | "choice"
    default: Any
    unit: str = ""
    minimum: float = 0.0
    maximum: float = 1e6
    step: float = 1.0
    decimals: int = 3
    choices: tuple[tuple[str, str], ...] = ()  # (value, label)
    visible_when: tuple[str, tuple[Any, ...]] | None = None  # (other key, allowed values)
    tooltip: str = ""


def get_param(params: dict, key: str, default: Any = None) -> Any:
    node: Any = params
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_param(params: dict, key: str, value: Any) -> None:
    parts = key.split(".")
    node = params
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _num(params: dict, key: str, default: float, lo: float = -math.inf, hi: float = math.inf) -> float:
    """Read a numeric parameter defensively (hand-edited files may be odd)."""
    try:
        value = float(get_param(params, key, default))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return min(max(value, lo), hi)


# ==========================================================================
# Build products
# ==========================================================================
@dataclass(frozen=True)
class PlacedPart:
    """A part positioned inside a unit (2D pose in the unit frame)."""

    part_id: str
    layer: Layer
    prims: tuple[Primitive, ...]  # oriented, in the part frame
    pose: Pose
    sprite_name: str
    roll: tuple[tuple[float, ...], ...] | None = None  # orientation applied to the part (for CAD meshes)
    z_offset: float = 0.0  # vertical shift applied to the part (for CAD meshes)

    @cached_property
    def top(self) -> float:
        """Highest point of the part (its own frame), used for draw order and shadows."""
        return max(float(bounds3d(p)[1][2]) for p in self.prims)

    def footprint(self):
        return transform_geometry(footprint(self.prims), self.pose)

    def beam_section(self):
        return transform_geometry(slice_at(self.prims, 0.0), self.pose)


@dataclass(frozen=True)
class OpticVisual:
    """How to draw the optic held by a mount (vector overlay on the canvas)."""

    kind: OpticKind
    pose: Pose  # unit frame; origin at the front-surface centre, +x out of the front
    diameter: float
    thickness: float
    power_sign: int = 0  # +1 converging, -1 diverging (lens / curved mirror)


@dataclass(frozen=True)
class ForkPlacement:
    """Resolved clamping-fork placement for a pedestal base."""

    angle: float  # slot direction, degrees, unit frame
    screw_distance: float  # pedestal centre to screw axis
    hole: tuple[float, float] | None = None  # world hole used (None: no hole in reach)


@dataclass(frozen=True)
class UnitBuild:
    parts: tuple[PlacedPart, ...]
    elements: tuple[OpticalElement, ...] = ()
    emitters: tuple[Emitter, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    optic_visuals: tuple[OpticVisual, ...] = ()
    notes: tuple[str, ...] = ()

    @cached_property
    def _footprints(self) -> dict[Layer, object]:
        result = {}
        for layer in Layer:
            shapes = [p.footprint() for p in self.parts if p.layer is layer]
            result[layer] = as_polygonal(shapely.union_all(shapes)) if shapes else Polygon()
        return result

    def footprint(self, layer: Layer):
        """Union of the part footprints in one layer (unit frame, cached)."""
        return self._footprints[layer]

    @cached_property
    def pose_cache(self) -> dict:
        """World-frame results keyed by pose (filled by the engine; see world.py)."""
        return {}


@dataclass(frozen=True)
class UnitType:
    """A placeable unit.  Its build never includes a pedestal's clamping fork:
    the fork swivels independently (see :func:`fork_parts`), so moving a
    pedestal unit never forces a rebuild."""

    id: str
    name: str
    category: str
    description: str
    mounting: Mounting
    label_prefix: str
    bill_of_materials: tuple[str, ...]
    builder: Callable[["UnitType", Component], UnitBuild]
    orientation_part: str | None = None  # part whose taps give the orientation choices
    fields: tuple[ParamField, ...] = ()

    def orientations(self) -> list[tuple[str, str]]:
        if not self.orientation_part:
            return []
        return [(tap.name, tap.label) for tap in get_part(self.orientation_part).taps]

    def default_params(self) -> dict:
        params: dict = {}
        for f in self.fields:
            set_param(params, f.key, f.default)
        return params

    def build(self, component: Component) -> UnitBuild:
        return self.builder(self, component)


# ==========================================================================
# Optic configuration
# ==========================================================================
OPTIC_KIND_CHOICES = tuple((k.value, k.label) for k in OpticKind)

# Typical Ø1" optic thickness per kind, used when the thickness is left on "Auto".
DEFAULT_THICKNESS_MM = {
    OpticKind.MIRROR: 6.0,
    OpticKind.LENS: 4.0,
    OpticKind.SPLITTER: 3.0,
    OpticKind.WINDOW: 3.0,
    OpticKind.BLOCK: 6.0,
}


def optic_fields(
    default_kind: OpticKind, kinds: tuple[OpticKind, ...] | None = None, with_thickness: bool = True
) -> tuple[ParamField, ...]:
    choices = tuple((k.value, k.label) for k in (kinds or tuple(OpticKind)))
    thickness = (
        ParamField(
            "optic.thickness_mm",
            "Optic thickness",
            "float",
            None,
            unit="mm",
            minimum=0.1,
            maximum=25.0,
            step=0.5,
            decimals=2,
            visible_when=("optic.kind", ("mirror", "lens", "splitter", "window", "block")),
            tooltip="Auto = typical Ø1\" optic (mirror 6 mm, lens 4 mm, plate 3 mm). "
            "It moves the surface: front-loaded mounts push thicker optics forward.",
        ),
    )
    return (
        ParamField("optic.kind", "Optic", "choice", default_kind.value, choices=choices),
        ParamField(
            "optic.roc_mm",
            "Radius of curvature",
            "float",
            0.0,
            unit="mm",
            minimum=-1e5,
            maximum=1e5,
            step=10.0,
            decimals=1,
            visible_when=("optic.kind", ("mirror",)),
            tooltip="0 = flat mirror; positive = concave (focusing)",
        ),
        ParamField(
            "optic.focal_length_mm",
            "Focal length",
            "float",
            100.0,
            unit="mm",
            minimum=-1e5,
            maximum=1e5,
            step=5.0,
            decimals=1,
            visible_when=("optic.kind", ("lens",)),
            tooltip="positive = converging lens",
        ),
        ParamField(
            "optic.reflectance",
            "Reflectance R",
            "float",
            0.5,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            decimals=3,
            visible_when=("optic.kind", ("splitter",)),
        ),
        ParamField(
            "optic.transmittance",
            "Transmittance T",
            "float",
            1.0,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            decimals=3,
            visible_when=("optic.kind", ("window",)),
        ),
    ) + (thickness if with_thickness else ())


def _optic_kind(params: dict, default: OpticKind) -> OpticKind:
    try:
        return OpticKind(get_param(params, "optic.kind", default.value))
    except ValueError:
        return default


def optic_thickness(params: dict, kind: OpticKind) -> float:
    """Optic thickness in inches (the user's value, or typical for the kind)."""
    default_mm = DEFAULT_THICKNESS_MM.get(kind, 6.0)
    value = get_param(params, "optic.thickness_mm", None)
    mm = default_mm if value is None else _num(params, "optic.thickness_mm", default_mm, 0.05, 50.0)
    return mm * MM


def _optic_elements(
    part: PartSpec, pose: Pose, params: dict, default_kind: OpticKind, owner_name: str
) -> tuple[list[OpticalElement], list[Blocker], list[OpticVisual]]:
    """Optical element(s), substrate blocker and drawing for a mount's optic.

    ``pose`` places the mount's part frame in the unit frame.
    """
    slot = part.optic_slot
    kind = _optic_kind(params, default_kind)
    if slot is None or kind is OpticKind.NONE:
        return [], [], []
    thickness = PELLICLE_THICKNESS if part.id in PELLICLE_MOUNTS else optic_thickness(params, kind)
    half_ca = slot.clear_aperture / 2
    roc_mm = _num(params, "optic.roc_mm", 0.0)
    f_mm = _num(params, "optic.focal_length_mm", 100.0)

    # Reflective optics act at their front surface, transmissive ones at their mid-plane.
    front_x = slot.front_surface_x(thickness)
    reflective = kind in (OpticKind.MIRROR, OpticKind.SPLITTER, OpticKind.BLOCK)
    x_surface = front_x if reflective else front_x - thickness / 2
    surface_pose = pose.compose(Pose(x_surface, 0.0, 0.0))
    element = OpticalElement(
        kind=kind,
        center=(surface_pose.x, surface_pose.y),
        normal_angle=surface_pose.angle,
        half_aperture=slot.diameter / 2 if kind is OpticKind.BLOCK else half_ca,
        focal_length=f_mm * MM if kind is OpticKind.LENS and f_mm != 0 else math.inf,
        roc=roc_mm * MM if kind is OpticKind.MIRROR and roc_mm != 0 else math.inf,
        reflectance=_num(params, "optic.reflectance", 0.5, 0.0, 1.0) if kind is OpticKind.SPLITTER else 0.0,
        transmittance=_num(params, "optic.transmittance", 1.0, 0.0, 1.0) if kind is OpticKind.WINDOW else 1.0,
        name=f"{owner_name} {kind.label.lower()}".strip(),
    )
    blockers: list[Blocker] = []
    if kind in (OpticKind.MIRROR, OpticKind.BLOCK):
        # Opaque substrate behind the surface (keeps beams from leaking through the back).
        substrate = box(front_x - thickness, -slot.diameter / 2, front_x - 0.003, slot.diameter / 2)
        blockers.append(Blocker(transform_geometry(substrate, pose)))
    power_sign = 0
    if kind is OpticKind.LENS and f_mm != 0:
        power_sign = 1 if f_mm > 0 else -1
    if kind is OpticKind.MIRROR and roc_mm != 0:
        power_sign = 1 if roc_mm > 0 else -1
    visual_pose = pose.compose(Pose(front_x, 0.0, 0.0))
    visual = OpticVisual(kind, visual_pose, slot.diameter, max(thickness, 0.02), power_sign)
    return [element], blockers, [visual]


PELLICLE_MOUNTS = frozenset({"KM100BP"})
PELLICLE_THICKNESS = 2e-6 / 0.0254  # ~2 µm nitrocellulose membrane


# ==========================================================================
# Building blocks
# ==========================================================================
def _as_tuple(matrix) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(float(v) for v in row) for row in np.asarray(matrix))


def _placed(part_id: str, pose: Pose = Pose(), roll: np.ndarray | None = None, z_offset: float = 0.0) -> PlacedPart:
    """Place a part, optionally re-oriented by ``roll`` and raised by ``z_offset``."""
    part = get_part(part_id)
    prims = part.prims
    if roll is not None or z_offset:
        rot = np.eye(3) if roll is None else roll
        prims = transform_all(prims, rot, (0.0, 0.0, z_offset))
    name = part_id if roll is None and not z_offset else f"{part_id}-r{hash((prims,)) & 0xFFFF:04x}"
    return PlacedPart(
        part_id, part.layer, prims, pose, name, roll=None if roll is None else _as_tuple(roll), z_offset=z_offset
    )


def _mount_on_post(part_id: str, tap_name: str | None) -> tuple[PlacedPart, Pose]:
    """Place a mount so its chosen tap sits on the post axis (unit origin)."""
    part = get_part(part_id)
    tap = part.tap(tap_name)
    oriented = part.oriented(tap.name)
    pose = Pose(-oriented.post_xy[0], -oriented.post_xy[1], 0.0)
    placed = PlacedPart(
        part_id, part.layer, oriented.prims, pose, f"{part_id}-{tap.name}", roll=_as_tuple(oriented.rotation)
    )
    return placed, pose


THUMBSCREW_ANGLE = 180.0  # PH2 thumbscrew points to the unit's -y side


def _post_holder_parts(on_pedestal: bool) -> list[PlacedPart]:
    return [_placed("PH2@BE1" if on_pedestal else "PH2", Pose(0, 0, THUMBSCREW_ANGLE))]


def default_fork() -> ForkPlacement:
    lo, hi = P.cf125c_screw_range()
    return ForkPlacement(angle=180.0, screw_distance=(lo + hi) / 2)


def _pedestal_parts() -> list[PlacedPart]:
    """BE1 + PH2; the clamping fork is added per placement by :func:`fork_parts`."""
    return [_placed("BE1"), *_post_holder_parts(on_pedestal=True)]


def fork_parts(fork: ForkPlacement) -> tuple[PlacedPart, ...]:
    """CF125C fork and its cap screw for a resolved fork placement (unit frame)."""
    rad = math.radians(fork.angle)
    screw = Pose(fork.screw_distance * math.cos(rad), fork.screw_distance * math.sin(rad), 0.0)
    return (_placed("CF125C", Pose(0, 0, fork.angle)), _placed("SCREW", screw))


_FORK_SHAPE = None


def fork_footprint(fork: ForkPlacement):
    """Fork footprint in the unit frame (the screw head lies within the fork)."""
    global _FORK_SHAPE
    if _FORK_SHAPE is None:
        _FORK_SHAPE = get_part("CF125C").footprint()
    return transform_geometry(_FORK_SHAPE, Pose(0.0, 0.0, fork.angle))


def _beam_blockers(placed: list[PlacedPart]) -> list[Blocker]:
    blockers = []
    for p in placed:
        if p.layer is Layer.OPTIC:
            section = p.beam_section()
            if not section.is_empty:
                blockers.append(Blocker(section))
    return blockers


# ==========================================================================
# Builders
# ==========================================================================
def build_post_mounted_optic(
    unit: UnitType, comp: Component, *, mount_id: str, adapter_id: str | None, default_kind: OpticKind
) -> UnitBuild:
    pedestal = unit.mounting is Mounting.PEDESTAL
    parts: list[PlacedPart] = _pedestal_parts() if pedestal else _post_holder_parts(False)
    mount, mount_pose = _mount_on_post(mount_id, comp.orientation or None)
    if adapter_id:
        # Adapter (e.g. KB1X1) sits on the post directly under the mount's tap.
        mount_tap_z = get_part(mount_id).oriented(comp.orientation or None).tap_z
        parts.append(_placed(adapter_id, Pose(), z_offset=mount_tap_z))
    parts.append(mount)
    if mount_id in PELLICLE_MOUNTS and _optic_kind(comp.params, default_kind) is not OpticKind.NONE:
        # The pellicle's own frame sits on the mount's front face.
        parts.append(_placed("BP108", mount_pose.compose(Pose(P.PELLICLE_PLANE_X, 0.0, 0.0))))
    elements, substrate, visuals = _optic_elements(
        get_part(mount_id), mount_pose, comp.params, default_kind, comp.label
    )
    blockers = _beam_blockers(parts) + substrate
    return UnitBuild(
        parts=tuple(parts),
        elements=tuple(elements),
        blockers=tuple(blockers),
        optic_visuals=tuple(visuals),
    )


def build_base(unit: UnitType, comp: Component) -> UnitBuild:
    return UnitBuild(parts=tuple(_pedestal_parts()))


# Collimator = SM1ZB (fiber input, hanging inverted from its PH2) + four ER cage
# rods (length is a parameter; ER1.5 by default) + CP35 cage plate holding the
# lens.  Both plates clamp the rods with setscrews, so the CP35 slides along
# them; the rods must still pass through the SM1ZB plate.
ROD_MIN_ENGAGEMENT = 0.15  # inches of rod that must stay inside the SM1ZB plate
DEFAULT_ROD_LENGTH = 1.5  # ER1.5
ROD_LENGTH_RANGE = (0.5, 24.0)


def collimator_lens_range(orientation: str | None, rod_length: float = DEFAULT_ROD_LENGTH) -> tuple[float, float]:
    """Feasible fiber -> lens distances (inches) for a rod length.

    With the housing toward the lens the CP35 must clear the 0.80"-deep
    housing, so the range shifts to longer focal lengths.
    """
    flipped = get_part("SM1ZB").tap(orientation).yaw == 180
    tip_x = -P.SM1ZB_FIBER_X if flipped else P.SM1ZB_FIBER_X
    lens_side_obstacle = -P.SM1ZB_HOUSING_BACK if flipped else P.SM1ZB_CARRIAGE_X
    half_plate = P.CP35_THICKNESS / 2
    lens_min = lens_side_obstacle + half_plate
    lens_max = P.SM1ZB_PLATE_HALF_T - ROD_MIN_ENGAGEMENT + rod_length - half_plate
    return lens_min - tip_x, max(lens_max, lens_min) - tip_x


def build_collimator(unit: UnitType, comp: Component) -> UnitBuild:
    params = comp.params
    sm1zb_part = get_part("SM1ZB")
    tap = sm1zb_part.tap(comp.orientation or None)
    rod_length = _num(params, "rods.length_in", DEFAULT_ROD_LENGTH, *ROD_LENGTH_RANGE)
    d_min, d_max = collimator_lens_range(tap.name, rod_length)
    f_mm = _num(params, "lens.focal_length_mm", 25.4, -1e4, 1e4)
    wanted = _num(params, "lens.distance_mm", 25.4, 0.0, 2000.0) * MM
    distance = min(max(wanted, d_min), d_max)

    parts: list[PlacedPart] = _post_holder_parts(on_pedestal=False)
    sm1zb, frame = _mount_on_post("SM1ZB", tap.name)  # the post sits right under the optic axis
    parts.append(sm1zb)
    tip_x = -P.SM1ZB_FIBER_X if tap.yaw == 180 else P.SM1ZB_FIBER_X
    # The fiber connector always sticks out of the plate face on the fiber side.
    parts.append(_placed("FIBER", frame.compose(Pose(-P.SM1ZB_PLATE_HALF_T, 0.0, 0.0))))

    lens_x = tip_x + distance  # lens mid-plane, centred in the CP35
    cp35_front = lens_x + P.CP35_THICKNESS / 2
    roll = P.roll_bringing_down(tap.normal)  # CP35 is symmetric end-for-end: roll only
    cp35_pose = frame.compose(Pose(cp35_front, 0.0, 0.0))
    parts.append(_placed("CP35", cp35_pose, roll=roll))
    # Rods flush with the CP35's front face, reaching back through the SM1ZB plate.
    rod_center = cp35_front - rod_length / 2
    c = P.CAGE_SPACING / 2
    for y in (-c, c):
        for z in (-c, c):
            parts.append(_cage_rod(rod_length, frame.compose(Pose(rod_center, y, 0.0)), z))

    lens_params = {"optic": {"kind": "lens", "focal_length_mm": f_mm}}
    # The lens sits inside the cage plate: it acts on the beam but is not drawn.
    elements, _, _ = _optic_elements(get_part("CP35"), cp35_pose, lens_params, OpticKind.LENS, comp.label)
    tip = frame.compose(Pose(tip_x, 0.0, 0.0))
    emitters: tuple[Emitter, ...] = ()
    if bool(get_param(params, "source.enabled", True)):
        mfd_um = _num(params, "source.mfd_um", 5.0, 0.1, 1000.0)
        emitters = (
            Emitter(
                position=(tip.x, tip.y),
                angle=tip.angle,
                wavelength=_num(params, "source.wavelength_nm", 780.0, 100.0, 20000.0) * NM,
                waist_radius=mfd_um / 2 * UM,
                waist_distance=0.0,
                m2=1.0,
                power=_num(params, "source.power_mw", 1.0, 0.0, 1e6),
                name=f"{comp.label} fiber".strip(),
            ),
        )
    notes = [
        f"Fiber → lens: {distance / MM:.1f} mm ({distance:.3f}\")",
        f"{rod_length:g}\" rods allow {d_min / MM:.1f}–{d_max / MM:.1f} mm in this orientation",
    ]
    if abs(wanted - distance) > 1e-6:
        notes.append(f"Requested {wanted / MM:.1f} mm is out of reach; clamped")
    notes = tuple(notes)
    return UnitBuild(
        parts=tuple(parts),
        elements=tuple(elements),
        emitters=emitters,
        blockers=tuple(_beam_blockers(parts)),
        notes=notes,
    )


def _cage_rod(length: float, pose: Pose, z: float) -> PlacedPart:
    """A plain Ø6 mm cage rod of any length (drawn from its own primitive)."""
    prims = transform_all(P.cage_rod_prims(length), np.eye(3), (0.0, 0.0, z))
    return PlacedPart("ER", Layer.OPTIC, prims, pose, f"ER-{length:g}in", z_offset=z)


def build_laser(unit: UnitType, comp: Component) -> UnitBuild:
    params = comp.params
    emitters: tuple[Emitter, ...] = ()
    if bool(get_param(params, "enabled", True)):
        emitters = (
            Emitter(
                position=(0.0, 0.0),
                angle=0.0,
                wavelength=_num(params, "wavelength_nm", 633.0, 100.0, 20000.0) * NM,
                waist_radius=_num(params, "waist_um", 500.0, 0.1, 1e5) * UM,
                waist_distance=_num(params, "waist_distance_in", 0.0, -1e4, 1e4),
                m2=_num(params, "m2", 1.0, 1.0, 100.0),
                power=_num(params, "power_mw", 1.0, 0.0, 1e6),
                name=f"{comp.label} source".strip(),
            ),
        )
    return UnitBuild(parts=(), emitters=emitters)


# ==========================================================================
# Registry
# ==========================================================================
def _mount_unit(
    uid: str,
    name: str,
    *,
    category: str,
    description: str,
    mounting: Mounting,
    prefix: str,
    mount_id: str,
    bom: tuple[str, ...],
    default_kind: OpticKind,
    adapter_id: str | None = None,
    kinds: tuple[OpticKind, ...] | None = None,
    optic_defaults: dict | None = None,
    optic_param_fields: tuple[ParamField, ...] | None = None,
) -> UnitType:
    def builder(unit: UnitType, comp: Component) -> UnitBuild:
        return build_post_mounted_optic(unit, comp, mount_id=mount_id, adapter_id=adapter_id, default_kind=default_kind)

    if optic_param_fields is not None:
        fields = list(optic_param_fields)
    else:
        fields = list(optic_fields(default_kind, kinds, with_thickness=mount_id not in PELLICLE_MOUNTS))
    if optic_defaults:
        fields = [
            ParamField(**{**f.__dict__, "default": optic_defaults[f.key]}) if f.key in optic_defaults else f
            for f in fields
        ]
    if mounting is Mounting.PEDESTAL:
        fields.append(
            ParamField(
                "fork.angle",
                "Fork direction",
                "float",
                None,
                unit="°",
                minimum=-360,
                maximum=360,
                step=15,
                decimals=1,
                tooltip="Preferred clamping-fork direction (unit frame); empty = automatic",
            )
        )
    return UnitType(
        id=uid,
        name=name,
        category=category,
        description=description,
        mounting=mounting,
        label_prefix=prefix,
        bill_of_materials=bom,
        builder=builder,
        orientation_part=mount_id,
        fields=tuple(fields),
    )


BASE_BOM = ("PH2", "BE1", "CF125C", 'TR-series Ø1/2" post')
# KM100BP + BP108: the optic is always the pellicle; only its split ratio is editable
# (other Ø1" BP1xx pellicles share the frame).
BP108_FIELDS = (
    ParamField(
        "optic.reflectance",
        "Reflectance R",
        "float",
        P.BP108_REFLECTANCE,
        minimum=0.0,
        maximum=1.0,
        step=0.01,
        decimals=3,
        tooltip="BP108: 8:92 (R:T), uncoated, 400–2400 nm",
    ),
)


def _build_registry() -> dict[str, UnitType]:
    units = [
        _mount_unit(
            "km-mirror",
            "Kinematic mirror",
            category="Mirrors",
            description="KM100 kinematic mount on a pedestal base (PH2 + BE1 + CF125C).",
            mounting=Mounting.PEDESTAL,
            prefix="M",
            mount_id="KM100",
            bom=("KM100", *BASE_BOM),
            default_kind=OpticKind.MIRROR,
        ),
        _mount_unit(
            "km-h-mirror",
            "Kinematic mirror, hole-mounted",
            category="Mirrors",
            description="KM100CP post-centred kinematic mount on a PH2 screwed into a hole.",
            mounting=Mounting.HOLE,
            prefix="M",
            mount_id="KM100CP",
            bom=("KM100CP", "PH2", 'TR-series Ø1/2" post'),
            default_kind=OpticKind.MIRROR,
        ),
        _mount_unit(
            "mirror",
            "Fixed mirror",
            category="Mirrors",
            description="FMP1 fixed mount on a pedestal base (PH2 + BE1 + CF125C).",
            mounting=Mounting.PEDESTAL,
            prefix="M",
            mount_id="FMP1",
            bom=("FMP1", *BASE_BOM),
            default_kind=OpticKind.MIRROR,
        ),
        _mount_unit(
            "h-mirror",
            "Fixed mirror, hole-mounted",
            category="Mirrors",
            description="FMP1 fixed mount on a PH2 screwed into a hole.",
            mounting=Mounting.HOLE,
            prefix="M",
            mount_id="FMP1",
            bom=("FMP1", "PH2", 'TR-series Ø1/2" post'),
            default_kind=OpticKind.MIRROR,
        ),
        _mount_unit(
            "mag-km-pellicle",
            "Pellicle beamsplitter (BP108)",
            category="Beamsplitters",
            description="BP108 8:92 pellicle in a KM100BP kinematic mount, on a KB1X1 magnetic base and pedestal base.",
            mounting=Mounting.PEDESTAL,
            prefix="BS",
            mount_id="KM100BP",
            adapter_id="KB1X1",
            bom=("KM100BP", "BP108", "KB1X1", *BASE_BOM),
            default_kind=OpticKind.SPLITTER,
            optic_param_fields=BP108_FIELDS,
        ),
        _mount_unit(
            "lens",
            "Lens",
            category="Lenses",
            description="Ø1\" lens in an FMP1 on a pedestal base (any FMP1 unit can hold a lens).",
            mounting=Mounting.PEDESTAL,
            prefix="L",
            mount_id="FMP1",
            bom=("FMP1", *BASE_BOM),
            default_kind=OpticKind.LENS,
        ),
        _mount_unit(
            "h-lens",
            "Lens, hole-mounted",
            category="Lenses",
            description="Ø1\" lens in an FMP1 on a PH2 screwed into a hole.",
            mounting=Mounting.HOLE,
            prefix="L",
            mount_id="FMP1",
            bom=("FMP1", "PH2", 'TR-series Ø1/2" post'),
            default_kind=OpticKind.LENS,
        ),
        UnitType(
            id="collimator",
            name="Fiber collimator",
            category="Sources",
            description="SM1ZB fiber z-translator hanging from a PH2, four ER cage rods (ER1.5 by default), CP35 cage plate with a Ø1\" lens.",
            mounting=Mounting.HOLE,
            label_prefix="FC",
            bill_of_materials=("SM1ZB", "PH2", "ER rods ×4", "CP35", "Ø1\" lens", "SM1 fiber adapter"),
            builder=build_collimator,
            orientation_part="SM1ZB",
            fields=(
                ParamField("source.enabled", "Emit beam", "bool", True),
                ParamField("source.wavelength_nm", "Wavelength", "float", 780.0, unit="nm", minimum=100, maximum=20000, step=1, decimals=1),
                ParamField("source.mfd_um", "Fiber mode-field dia.", "float", 5.0, unit="µm", minimum=0.1, maximum=1000, step=0.1, decimals=2),
                ParamField("source.power_mw", "Power", "float", 1.0, unit="mW", minimum=0, maximum=1e6, step=0.1, decimals=3),
                ParamField("lens.focal_length_mm", "Lens focal length", "float", 25.4, unit="mm", minimum=-1e4, maximum=1e4, step=1, decimals=2),
                ParamField(
                    "lens.distance_mm",
                    "Fiber → lens distance",
                    "float",
                    25.4,
                    unit="mm",
                    minimum=1.0,
                    maximum=2000,
                    step=0.1,
                    decimals=2,
                    tooltip="Equal to the focal length for a collimated beam. The CP35 slides along the "
                    "cage rods, which limits the range (see the notes below).",
                ),
                ParamField(
                    "rods.length_in",
                    "Cage rod length",
                    "float",
                    DEFAULT_ROD_LENGTH,
                    unit="in",
                    minimum=ROD_LENGTH_RANGE[0],
                    maximum=ROD_LENGTH_RANGE[1],
                    step=0.5,
                    decimals=2,
                    tooltip="ER-series rods: ER05 = 0.5\", ER1, ER1.5, ER2, ER3, ER4, ER6, ...",
                ),
            ),
        ),
        UnitType(
            id="laser",
            name="Beam source",
            category="Sources",
            description="A free-space Gaussian beam: wavelength, waist, waist position and direction.",
            mounting=Mounting.FREE,
            label_prefix="S",
            bill_of_materials=(),
            builder=build_laser,
            fields=(
                ParamField("enabled", "Emit beam", "bool", True),
                ParamField("wavelength_nm", "Wavelength", "float", 633.0, unit="nm", minimum=100, maximum=20000, step=1, decimals=1),
                ParamField("waist_um", "Waist radius w₀", "float", 500.0, unit="µm", minimum=0.1, maximum=1e5, step=10, decimals=1),
                ParamField(
                    "waist_distance_in",
                    "Waist position",
                    "float",
                    0.0,
                    unit="in",
                    minimum=-1e4,
                    maximum=1e4,
                    step=0.5,
                    decimals=3,
                    tooltip="Distance of the waist downstream of the source (negative: behind it)",
                ),
                ParamField("m2", "M²", "float", 1.0, minimum=1.0, maximum=100, step=0.1, decimals=2),
                ParamField("power_mw", "Power", "float", 1.0, unit="mW", minimum=0, maximum=1e6, step=0.1, decimals=3),
            ),
        ),
        UnitType(
            id="base",
            name="Pedestal base",
            category="Bases",
            description="PH2 post holder on a BE1 pedestal adapter, clamped with a CF125C fork.",
            mounting=Mounting.PEDESTAL,
            label_prefix="B",
            bill_of_materials=BASE_BOM,
            builder=build_base,
            fields=(
                ParamField(
                    "fork.angle", "Fork direction", "float", None, unit="°", minimum=-360, maximum=360, step=15,
                    decimals=1, tooltip="Preferred clamping-fork direction; empty = automatic",
                ),
            ),
        ),
    ]
    return {u.id: u for u in units}


UNIT_TYPES: dict[str, UnitType] = _build_registry()
CATEGORY_ORDER = ("Sources", "Mirrors", "Beamsplitters", "Lenses", "Bases")


def get_unit_type(type_id: str) -> UnitType:
    try:
        return UNIT_TYPES[type_id]
    except KeyError as exc:
        raise KeyError(f"unknown unit type {type_id!r}") from exc


def new_component_params(type_id: str) -> dict:
    return get_unit_type(type_id).default_params()


def catalog_parts() -> list[PlacedPart]:
    """Every distinct part orientation the catalog produces: each unit in each
    orientation with default parameters, plus the pedestal clamping fork."""
    seen: dict[tuple, PlacedPart] = {}
    for unit in UNIT_TYPES.values():
        for orientation in [name for name, _ in unit.orientations()] or [""]:
            comp = Component("catalog", unit.id, orientation=orientation, params=unit.default_params())
            parts = unit.build(comp).parts
            if unit.mounting is Mounting.PEDESTAL:
                parts += fork_parts(default_fork())
            for part in parts:
                seen.setdefault((part.part_id, part.prims, part.roll, part.z_offset), part)
    return list(seen.values())
