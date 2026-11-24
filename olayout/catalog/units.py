"""Combined units: what the user actually places on the breadboard.

Each :class:`UnitType` knows its bill of materials, how it is fastened to
the table (:class:`Mounting`), its editable parameters (:class:`ParamField`)
and how to *build* itself: a :class:`UnitBuild` listing the placed parts,
optical elements, emitters and beam blockers in the unit frame.

Unit frame: origin on the post axis (table level), +x is the direction the
optic's front face points (for sources: the beam direction).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from enum import Enum
from functools import cached_property, partial
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
    rotation_matrix,
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
class Stretch:
    """A part that is uniform along x (a rail, a board, a cage rod) is drawn by
    rendering a short piece of it once and stretching its middle to the real
    length, so every length draws instantly and looks the same."""

    template: tuple[Primitive, ...]  # the same part, but short
    template_span: tuple[float, float]  # x extent of the template's solid
    span: tuple[float, float]  # x extent of the real part


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
    stretch: Stretch | None = None  # drawn by stretching a short template (see Stretch)

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
    # Lifted off (a mount taken off its KB1X1): drawn faintly, but no optics or collisions.
    detached_parts: tuple[PlacedPart, ...] = ()
    # Space taken in a layer by something drawn in the other one: a post or board
    # standing on the table reaches up through the beam plane.
    keepouts: tuple[tuple[Layer, Polygon], ...] = ()

    @cached_property
    def _footprints(self) -> dict[Layer, object]:
        result = {}
        for layer in Layer:
            shapes = [p.footprint() for p in self.parts if p.layer is layer]
            shapes += [shape for keep_layer, shape in self.keepouts if keep_layer is layer]
            result[layer] = as_polygonal(shapely.union_all(shapes)) if shapes else Polygon()
        return result

    def footprint(self, layer: Layer):
        """Union of the part footprints (and keep-outs) in one layer (unit frame, cached)."""
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
    # A straight member running along +x from its origin (a rail, a board): the
    # parameter holding its length.  It is drawn end to end when placed.
    length_key: str | None = None
    # Frame members and boards are joined to one another (corners, boards in
    # slots), so they never collide with each other; they still do with the rest.
    structural: bool = False
    hole_anchor: str = "Post holder"  # what sits on the hole, for a hole-mounted unit
    preview_params: tuple[tuple[str, Any], ...] = ()  # (key, value) overrides for the library thumbnail

    def orientations(self) -> list[tuple[str, str]]:
        if not self.orientation_part:
            return []
        return [(tap.name, tap.label) for tap in get_part(self.orientation_part).taps]

    def default_params(self) -> dict:
        params: dict = {}
        for f in self.fields:
            set_param(params, f.key, f.default)
        return params

    def member_length(self, params: dict) -> float | None:
        """A straight member's length (see ``length_key``), clamped to its field's range."""
        if not self.length_key:
            return None
        field = next(f for f in self.fields if f.key == self.length_key)
        return _num(params, field.key, field.default, field.minimum, field.maximum)

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
    # A stable name across runs (hash() of strings is randomised per process).
    variant = hashlib.sha1(repr(prims).encode()).hexdigest()[:6]
    name = part_id if roll is None and not z_offset else f"{part_id}-r{variant}"
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
# A mount on a KB1X1 magnetic base can be lifted off (with the base's top
# plate) and put back repeatably; while it is off it does nothing optically.
KB1X1_SEATED = ParamField(
    "kb1x1.seated",
    "On its KB1X1",
    "bool",
    True,
    tooltip="Untick (or press K) to lift the mount off its KB1X1 magnetic base: it stops "
    "reflecting, blocking and colliding until you put it back.",
)


def has_kb1x1(unit: UnitType) -> bool:
    return any(f.key == KB1X1_SEATED.key for f in unit.fields)


def lifted_off(comp: Component) -> bool:
    """Whether a KB1X1-mounted unit's mount is currently off its base."""
    return not bool(get_param(comp.params, KB1X1_SEATED.key, True))


def build_post_mounted_optic(
    unit: UnitType, comp: Component, *, mount_id: str, adapter_id: str | None, default_kind: OpticKind
) -> UnitBuild:
    pedestal = unit.mounting is Mounting.PEDESTAL
    parts: list[PlacedPart] = _pedestal_parts() if pedestal else _post_holder_parts(False)
    mount, mount_pose = _mount_on_post(mount_id, comp.orientation or None)
    carried = [mount]  # what sits on the adapter (and comes off with a KB1X1's top plate)
    if mount_id in PELLICLE_MOUNTS and _optic_kind(comp.params, default_kind) is not OpticKind.NONE:
        # The pellicle's own frame sits on the mount's front face.
        carried.append(_placed("BP108", mount_pose.compose(Pose(P.PELLICLE_PLANE_X, 0.0, 0.0))))
    if adapter_id:
        # Adapter (e.g. KB1X1) sits on the post directly under the mount's tap.
        mount_tap_z = get_part(mount_id).oriented(comp.orientation or None).tap_z
        if adapter_id == "KB1X1" and lifted_off(comp):
            parts.append(_placed("KB1X1-BOTTOM", Pose(), z_offset=mount_tap_z))
            return UnitBuild(
                parts=tuple(parts),
                blockers=tuple(_beam_blockers(parts)),
                notes=("Lifted off its KB1X1: no optics or collisions until it is put back (K)",),
                detached_parts=tuple(carried),
            )
        parts.append(_placed(adapter_id, Pose(), z_offset=mount_tap_z))
    parts += carried
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


def build_beam_expander(unit: UnitType, comp: Component, *, mirrored: bool) -> UnitBuild:
    """BE02R: the beam travels along +x and meets mirror 1 at the unit origin.

    The mirrored version is the same expander upside down, so its beam path
    folds the other way (y -> -y).
    """
    flip = -1.0 if mirrored else 1.0
    parts = [_placed("BE02R", Pose(), roll=rotation_matrix("x", 180) if mirrored else None)]
    elements, visuals = [], []
    for index, mirror in enumerate(P.BE02R_MIRRORS, start=1):
        x, y = mirror.center
        angle = mirror.normal_angle * flip
        lo, hi = mirror.aperture
        offset = (lo + hi) / 2 * flip  # mirroring reverses the tangent
        elements.append(
            OpticalElement(
                kind=OpticKind.MIRROR,
                center=(x, y * flip),
                normal_angle=angle,
                half_aperture=(hi - lo) / 2,
                aperture_offset=offset,
                roc=mirror.roc,
                name=f"{comp.label} mirror {index}".strip(),
            )
        )
        tangent = math.radians(angle + 90.0)
        centre = Pose(x + offset * math.cos(tangent), y * flip + offset * math.sin(tangent), angle)
        visuals.append(OpticVisual(OpticKind.MIRROR, centre, hi - lo, mirror.thickness, 1 if mirror.roc > 0 else -1))
    side = "right" if mirrored else "left"
    first, second = P.BE02R_MIRRORS
    edge = min(-first.aperture[0], first.aperture[1])  # mirror 1's edge nearest the beam axis
    across = edge * abs(math.cos(math.radians(first.normal_angle)))  # as the obliquely incident beam sees it
    notes = (
        f"2X: convex R {-first.roc / MM:.1f} mm, then concave R {second.roc / MM:.0f} mm",
        f"The beam leaves {P.BE02R_EXIT_ANGLE:.1f}° to the {side} of its input direction",
        # A Gaussian loses 1% past an edge 1.645 w / √2 from its centre.
        f"Mirror 1's edge is {edge / MM:.1f} mm from the axis: beams over ~Ø{2 * math.sqrt(2) * across / 1.645 / MM:.1f} mm clip",
    )
    return UnitBuild(
        parts=tuple(parts),
        elements=tuple(elements),
        blockers=tuple(_beam_blockers(parts)),
        optic_visuals=tuple(visuals),
        notes=notes,
    )


# Collimator = SM1ZB (fiber input, hanging inverted from its PH2) + four ER cage
# rods (length is a parameter; ER1.5 by default) + CP35 cage plate holding the
# lens.  Both plates clamp the rods with setscrews, so the CP35 slides along
# them; the rods must still pass through the SM1ZB plate.
ROD_MIN_ENGAGEMENT = 0.15  # inches of rod that must stay inside the SM1ZB plate
DEFAULT_ROD_LENGTH = 1.5  # ER1.5
ROD_LENGTH_RANGE = (0.5, 24.0)


COLLIMATOR_MIN_DISTANCE = 5.0 * MM  # short-focus lenses sit recessed behind the CP35, in a lens tube


def _cp35_nearest(flipped: bool) -> float:
    """Closest position of the CP35's mid-plane (SM1ZB frame x): it must clear
    the carriage, or the 0.80"-deep housing when that faces the lens."""
    lens_side_obstacle = -P.SM1ZB_HOUSING_BACK if flipped else P.SM1ZB_CARRIAGE_X
    return lens_side_obstacle + P.CP35_THICKNESS / 2


def collimator_lens_range(orientation: str | None, rod_length: float = DEFAULT_ROD_LENGTH) -> tuple[float, float]:
    """Feasible fiber -> lens distances (inches) for a rod length.

    The far end is where the CP35 still holds the rods.  The near end is
    5 mm in either orientation: a lens closer than the CP35 itself can get
    sits recessed toward the fiber, in a lens tube.
    """
    flipped = get_part("SM1ZB").tap(orientation).yaw == 180
    tip_x = -P.SM1ZB_FIBER_X if flipped else P.SM1ZB_FIBER_X
    lens_max = P.SM1ZB_PLATE_HALF_T - ROD_MIN_ENGAGEMENT + rod_length - P.CP35_THICKNESS / 2
    far = max(lens_max, _cp35_nearest(flipped)) - tip_x
    return COLLIMATOR_MIN_DISTANCE, max(far, COLLIMATOR_MIN_DISTANCE)


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

    lens_x = tip_x + distance  # lens mid-plane
    cp35_x = max(lens_x, _cp35_nearest(tap.yaw == 180))  # the CP35 centres on the lens when it can
    cp35_front = cp35_x + P.CP35_THICKNESS / 2
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
    # The lens sits inside the cage plate (or recessed behind it): it acts on the beam but is not drawn.
    lens_pose = frame.compose(Pose(cp35_front + lens_x - cp35_x, 0.0, 0.0))
    elements, _, _ = _optic_elements(get_part("CP35"), lens_pose, lens_params, OpticKind.LENS, comp.label)
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
        f"Fiber → lens: {distance / MM:.2f} mm ({distance:.3f}\")",
        f"{rod_length:g}\" rods allow {d_min / MM:.1f}–{d_max / MM:.1f} mm in this orientation",
    ]
    if cp35_x - lens_x > 1e-6:
        notes.append(f"Lens recessed {(cp35_x - lens_x) / MM:.1f} mm behind the CP35's centre (lens tube)")
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


STRETCH_TEMPLATE = 1.0  # inches: the short piece a uniform part's sprite is rendered from


def _cage_rod(length: float, pose: Pose, z: float) -> PlacedPart:
    """A plain Ø6 mm cage rod of any length, centred on its pose."""
    def prims(rod_length: float) -> tuple[Primitive, ...]:
        return transform_all(P.cage_rod_prims(rod_length), np.eye(3), (0.0, 0.0, z))

    half, template_half = length / 2, STRETCH_TEMPLATE / 2
    stretch = Stretch(prims(STRETCH_TEMPLATE), (-template_half, template_half), (-half, half))
    return PlacedPart("ER", Layer.OPTIC, prims(length), pose, f"ER-{length:g}in", z_offset=z, stretch=stretch)


def _straight_member(part_id: str, layer: Layer, prims: Callable[[float], tuple], length: float) -> PlacedPart:
    """A part uniform along x from its origin to ``length`` (a rail, a board)."""
    stretch = Stretch(prims(STRETCH_TEMPLATE), (0.0, STRETCH_TEMPLATE), (0.0, length))
    return PlacedPart(part_id, layer, prims(length), Pose(), f"{part_id}-{length:g}in", stretch=stretch)


# Objective = LMM40X reflective objective, threaded into an M1RMSS adapter in a
# CP35 cage plate standing on its post.  The objective is reflective (a
# convex and a concave mirror), but in the beam plane it acts as an ideal
# lens: it is modelled as a thin lens of its effective focal length, placed
# at its principal plane so that a collimated beam focuses one working
# distance in front of the nose.  Its entrance pupil is the aperture; the
# central obscuration of the secondary mirror is not modelled.
#
# The CP35 is drawn in the collimator's orientation (rolled 180° about the
# optic axis), whose CAD sprite is committed: from above, a cage plate looks
# the same either way up, only the setscrews on its top edge are hidden.
CP35_SPRITE_ROLL = rotation_matrix("x", 180)

OBJECTIVE_FIELDS = (
    ParamField(
        "objective.focal_length_mm",
        "Effective focal length",
        "float",
        P.LMM40X_EFL_MM,
        unit="mm",
        minimum=0.5,
        maximum=1000.0,
        step=0.1,
        decimals=2,
        tooltip="LMM40X-P01: 5.0 mm (40X with a 200 mm tube lens)",
    ),
    ParamField(
        "objective.working_distance_mm",
        "Working distance",
        "float",
        P.LMM40X_WD_MM,
        unit="mm",
        minimum=0.0,
        maximum=1000.0,
        step=0.1,
        decimals=2,
        tooltip="Nose to focus. LMM40X-P01: 7.8 mm",
    ),
    ParamField(
        "objective.pupil_mm",
        "Entrance pupil Ø",
        "float",
        P.LMM40X_PUPIL_MM,
        unit="mm",
        minimum=0.1,
        maximum=50.0,
        step=0.1,
        decimals=2,
        tooltip="The aperture beams are clipped by. LMM40X-P01: 5.1 mm (NA 0.50)",
    ),
)


def build_objective(unit: UnitType, comp: Component) -> UnitBuild:
    """CP35 on the post (its mid-plane over the post axis), the M1RMSS ring
    threaded in flush with its front face, and the LMM40X screwed in until its
    shoulder seats on the ring, pointing along +x toward the sample."""
    params = comp.params
    pedestal = unit.mounting is Mounting.PEDESTAL
    parts: list[PlacedPart] = _pedestal_parts() if pedestal else _post_holder_parts(False)
    plate_front = P.CP35_THICKNESS / 2
    parts.append(_placed("CP35", Pose(plate_front, 0.0, 0.0), roll=CP35_SPRITE_ROLL))
    parts.append(_placed("M1RMSS", Pose(plate_front, 0.0, 0.0)))
    parts.append(_placed("LMM40X", Pose(plate_front, 0.0, 0.0)))
    nose = plate_front + P.LMM40X_NOSE

    efl = _num(params, "objective.focal_length_mm", P.LMM40X_EFL_MM, 0.5, 1000.0) * MM
    wd = _num(params, "objective.working_distance_mm", P.LMM40X_WD_MM, 0.0, 1000.0) * MM
    pupil = _num(params, "objective.pupil_mm", P.LMM40X_PUPIL_MM, 0.1, 50.0) * MM
    principal = nose + wd - efl  # the equivalent thin lens
    element = OpticalElement(
        kind=OpticKind.LENS,
        center=(principal, 0.0),
        normal_angle=0.0,
        half_aperture=pupil / 2,
        focal_length=efl,
        name=f"{comp.label} objective".strip(),
    )
    where = "in front of" if principal >= nose else "behind"
    notes = (
        f"Thin lens f = {efl / MM:.2f} mm, {abs(principal - nose) / MM:.1f} mm {where} the nose (principal plane)",
        f"A collimated beam focuses {wd / MM:.2f} mm in front of the nose; NA ≈ {pupil / (2 * efl):.2f}",
        "Reflective: its two mirrors are not traced and the central obscuration is ignored",
    )
    return UnitBuild(
        parts=tuple(parts),
        elements=(element,),
        blockers=tuple(_beam_blockers(parts)),
        notes=notes,
    )


# Knife-edge combiner = MRAK25-P01 prism mirror on a KM100B platform, on a
# pedestal base, its knife edge over the post.  Each silvered leg is a flat
# mirror reaching right up to the edge, so two beams arriving square to the
# output from opposite sides leave side by side, as close as their own sizes
# allow.  A beam's centre should stay 1.5 w from the edge (1.3 w loses 0.5%).
def build_knife_edge(unit: UnitType, comp: Component) -> UnitBuild:
    parts = [*_pedestal_parts(), _placed("KM100B"), _placed("MRAK25")]
    reach = P.MRAK25_LEG / math.sqrt(2)  # each leg runs back this far, and as far to its side
    elements, visuals = [], []
    for side, sign in (("left", 1.0), ("right", -1.0)):  # as seen looking along the output
        centre = (-reach / 2, sign * reach / 2)
        normal = 45.0 * sign  # each leg turns a beam coming from its side toward +x
        elements.append(
            OpticalElement(
                kind=OpticKind.MIRROR,
                center=centre,
                normal_angle=normal,
                half_aperture=P.MRAK25_LEG / 2,
                name=f"{comp.label} {side} leg".strip(),
            )
        )
        visuals.append(OpticVisual(OpticKind.MIRROR, Pose(*centre, normal), P.MRAK25_LEG, 0.012))
    # The glass behind the silvered legs, kept just behind them so the legs themselves reflect.
    glass = Polygon(P.mrak25_outline()).buffer(-0.003, join_style="mitre")
    holder = [p for p in parts if p.part_id != "MRAK25"]
    notes = (
        "Beams arriving square to the angle from either side leave along it, side by side",
        "Keep each beam's centre 1.5 w from the edge (at 1.3 w it loses 0.5%)",
        "MRAK25 and KM100B are drawn from catalog dimensions (no vendor CAD)",
    )
    return UnitBuild(
        parts=tuple(parts),
        elements=tuple(elements),
        blockers=(*_beam_blockers(holder), Blocker(glass)),
        optic_visuals=tuple(visuals),
        notes=notes,
    )


# Iris = ID25 on a post, in a post holder (on a pedestal base or in a hole).
# The opening passes beams (clipping the ones it is too small for); the
# blades and the housing stop everything else.
IRIS_FIELDS = (
    ParamField(
        "iris.aperture_mm",
        "Aperture Ø",
        "float",
        3.0,
        unit="mm",
        minimum=P.ID25_APERTURE_MM[0],
        maximum=P.ID25_APERTURE_MM[1],
        step=0.5,
        decimals=2,
        tooltip="ID25: Ø1 mm to Ø25 mm. Close it to about the beam's 1/e² diameter to centre a beam on it.",
    ),
)


def build_iris(unit: UnitType, comp: Component) -> UnitBuild:
    pedestal = unit.mounting is Mounting.PEDESTAL
    parts = [*(_pedestal_parts() if pedestal else _post_holder_parts(False)), _placed("ID25")]
    aperture = _num(comp.params, "iris.aperture_mm", 3.0, *P.ID25_APERTURE_MM) * MM
    half, bore = aperture / 2, P.ID25_OPENING
    element = OpticalElement(
        kind=OpticKind.WINDOW, center=(0.0, 0.0), normal_angle=0.0, half_aperture=half, name=f"{comp.label} iris".strip()
    )
    blades = [Blocker(box(-0.01, lo, 0.01, hi)) for lo, hi in ((half, bore), (-bore, -half)) if hi - lo > 1e-6]
    return UnitBuild(
        parts=tuple(parts),
        elements=(element,),
        blockers=(*_beam_blockers(parts), *blades),
        notes=(f"Open to Ø{aperture / MM:.1f} mm; the blades stop beams outside it",),
    )


# Enclosure framing.  What a member meets depends on its height: beams only
# cross the beam plane, mounts sit at beam height, bases on the table.  Posts
# and boards stand from the table to above the beams, so they take space in
# both layers and stop beams; a rail lying on the table is only in the way of
# bases, a rail at beam height only of mounts and beams.
RAIL_RUNS = (
    ("beam", "At beam height (blocks beams)"),
    ("table", "Lying on the table (beams pass over)"),
)


def build_tslot_rail(unit: UnitType, comp: Component) -> UnitBuild:
    length = unit.member_length(comp.params)
    if get_param(comp.params, "rail.run", "beam") == "table":
        rail = _straight_member("TSLOT-RAIL", Layer.BASE, lambda n: P.tslot_rail_prims(n, P.TSLOT_SIZE / 2), length)
        return UnitBuild(parts=(rail,), notes=(f"{length:g}\" rail on the table: beams pass over it",))
    rail = _straight_member("TSLOT-RAIL", Layer.OPTIC, lambda n: P.tslot_rail_prims(n, 0.0), length)
    return UnitBuild(
        parts=(rail,),
        blockers=tuple(_beam_blockers([rail])),
        notes=(f"{length:g}\" rail across the beam plane: it blocks beams",),
    )


def _standing(part: PlacedPart, notes: tuple[str, ...]) -> UnitBuild:
    """A post or board: drawn with the mounts, in the way of bases too, and it stops beams."""
    return UnitBuild(
        parts=(part,),
        blockers=tuple(_beam_blockers([part])),
        keepouts=((Layer.BASE, part.footprint()),),
        notes=notes,
    )


def build_tslot_post(unit: UnitType, comp: Component) -> UnitBuild:
    post = PlacedPart("TSLOT-POST", Layer.OPTIC, get_part("TSLOT-POST").prims, Pose(), "TSLOT-POST")
    return _standing(post, ("Upright post: it blocks beams and takes space on the table",))


def build_board(unit: UnitType, comp: Component) -> UnitBuild:
    length = unit.member_length(comp.params)
    thickness = _num(comp.params, "board.thickness_in", P.BOARD_THICKNESS, 0.02, 1.0)
    board = _straight_member("BOARD", Layer.OPTIC, lambda n: P.board_prims(n, thickness), length)
    return _standing(board, (f"{length:g}\" × {thickness:g}\" board: it blocks beams",))


# Sample stage = MAX311D NanoMax screwed to the breadboard, a sample on its
# platform.  The sample faces out of one of the platform's two free faces;
# the stage's travel (4 mm per axis) moves it toward the objective (focus)
# and across.  Only the sample moves: the drawing of the stage stays put.
STAGE_FACES = (
    ("x", "Facing away from the X drive"),
    ("y", "Facing away from the Y and Z drives"),
)
STAGE_YAW = {"x": 180.0, "y": 90.0}  # turns the chosen face toward +x (the unit's angle)
SAMPLE_KINDS = (OpticKind.BLOCK, OpticKind.MIRROR, OpticKind.SPLITTER, OpticKind.WINDOW, OpticKind.NONE)


def build_sample_stage(unit: UnitType, comp: Component) -> UnitBuild:
    params = comp.params
    face = get_param(params, "stage.face", "x")
    face = face if face in STAGE_YAW else "x"
    stage_pose = Pose(0.0, 0.0, STAGE_YAW[face])
    parts = [_placed("MAX311D", stage_pose)]

    half_travel = P.MAX311D_TRAVEL_MM / 2
    wanted = (_num(params, "stage.focus_mm", 0.0), _num(params, "stage.lateral_mm", 0.0))
    focus, lateral = (min(max(t, -half_travel), half_travel) for t in wanted)
    standoff = _num(params, "sample.standoff_mm", 0.0, -60.0, 60.0)
    fx, fy = stage_pose.apply(P.max311d_face_centre(face))
    surface = Pose(fx + (standoff + focus) * MM, fy + lateral * MM, 0.0)

    notes = [
        f"Sample surface {standoff:+.1f} mm from the platform face; travel {focus:+.2f} mm toward the "
        f"objective and {lateral:+.2f} mm across (±{half_travel:g} mm)",
        "The platform top is 2.46\" above the table: the sample is assumed raised to beam height",
    ]
    if any(abs(t) > half_travel + 1e-9 for t in wanted):
        notes.append(f"Requested travel is beyond ±{half_travel:g} mm: clamped")
    kind = _optic_kind(params, OpticKind.BLOCK)
    if kind is OpticKind.NONE:
        return UnitBuild(parts=tuple(parts), notes=tuple(notes))

    width = _num(params, "sample.width_mm", 25.4, 1.0, 100.0) * MM
    thickness = optic_thickness(params, kind)
    parts.append(
        PlacedPart("SAMPLE", Layer.OPTIC, P.sample_prims(width, thickness), surface, f"SAMPLE-{width:.3f}x{thickness:.3f}")
    )
    # The sample is the optic itself: only a reflecting or opaque one is solid behind its surface.
    sample = replace(get_part("SAMPLE"), optic_slot=P.OpticSlot(0.0, "rear", width, width))
    elements, substrate, visuals = _optic_elements(sample, surface, params, OpticKind.BLOCK, f"{comp.label} sample")
    return UnitBuild(
        parts=tuple(parts),
        elements=tuple(elements),
        blockers=tuple(substrate),
        optic_visuals=tuple(visuals),
        notes=tuple(notes),
    )


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
FORK_ANGLE = ParamField(
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
    if adapter_id == "KB1X1":
        fields.insert(0, KB1X1_SEATED)
    if optic_defaults:
        fields = [
            ParamField(**{**f.__dict__, "default": optic_defaults[f.key]}) if f.key in optic_defaults else f
            for f in fields
        ]
    if mounting is Mounting.PEDESTAL:
        fields.append(FORK_ANGLE)
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


def _length_field(prefix: str, default: float) -> ParamField:
    return ParamField(
        f"{prefix}.length_in", "Length", "float", default, unit="in", minimum=0.5, maximum=144.0, step=0.5,
        decimals=3, tooltip="From the component's position along its angle",
    )


def _sample_fields() -> tuple[ParamField, ...]:
    optic = [
        ParamField(**{**f.__dict__, "default": 1.0, "tooltip": "Auto = 1 mm"}) if f.key == "optic.thickness_mm" else f
        for f in optic_fields(OpticKind.BLOCK, SAMPLE_KINDS)
    ]
    return (
        ParamField(
            "stage.face", "Sample face", "choice", "x", choices=STAGE_FACES,
            tooltip="Which free face of the platform the sample looks out of (the two are mirror images)",
        ),
        *optic,
        ParamField("sample.width_mm", "Sample width", "float", 25.4, unit="mm", minimum=1.0, maximum=100.0, step=1.0,
                   decimals=2),
        ParamField(
            "sample.standoff_mm", "Sample position", "float", 0.0, unit="mm", minimum=-60.0, maximum=60.0, step=0.5,
            decimals=2, tooltip="Sample surface beyond the platform's face (negative: over the platform)",
        ),
        ParamField(
            "stage.focus_mm", "Travel toward objective", "float", 0.0, unit="mm", minimum=-2.0, maximum=2.0,
            step=0.01, decimals=3, tooltip="The stage's travel along the sample's normal: ±2 mm",
        ),
        ParamField(
            "stage.lateral_mm", "Travel across", "float", 0.0, unit="mm", minimum=-2.0, maximum=2.0, step=0.01,
            decimals=3, tooltip="The stage's travel across the sample: ±2 mm",
        ),
    )


SAMPLE_STAGE_FIELDS = _sample_fields()


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
            "sq-km-mirror",
            "Kinematic square mirror",
            category="Mirrors",
            description='PFSQ10-03-P01 1" x 1" protected silver mirror (6 mm thick) in a KMSR compact kinematic mount, '
            "on a pedestal base (PH2 + BE1 + CF125C).",
            mounting=Mounting.PEDESTAL,
            prefix="M",
            mount_id="KMSR",
            bom=("KMSR", "PFSQ10-03-P01", *BASE_BOM),
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
            "kb-mirror",
            "Fixed mirror, magnetic base",
            category="Mirrors",
            description="FMP1 fixed mount on a KB1X1 magnetic base, on a pedestal base (PH2 + BE1 + CF125C). "
            "Lift it off and put it back with K.",
            mounting=Mounting.PEDESTAL,
            prefix="M",
            mount_id="FMP1",
            adapter_id="KB1X1",
            bom=("FMP1", "KB1X1", *BASE_BOM),
            default_kind=OpticKind.MIRROR,
        ),
        _mount_unit(
            "kb-h-mirror",
            "Fixed mirror, magnetic base, hole-mounted",
            category="Mirrors",
            description="FMP1 fixed mount on a KB1X1 magnetic base, on a PH2 screwed into a hole. "
            "Lift it off and put it back with K.",
            mounting=Mounting.HOLE,
            prefix="M",
            mount_id="FMP1",
            adapter_id="KB1X1",
            bom=("FMP1", "KB1X1", "PH2", 'TR-series Ø1/2" post'),
            default_kind=OpticKind.MIRROR,
        ),
        _mount_unit(
            "mag-km-pellicle",
            "Pellicle beamsplitter (BP108)",
            category="Beamsplitters",
            description="BP108 8:92 pellicle in a KM100BP kinematic mount, on a KB1X1 magnetic base and pedestal base. "
            "Lift it off and put it back with K.",
            mounting=Mounting.PEDESTAL,
            prefix="BS",
            mount_id="KM100BP",
            adapter_id="KB1X1",
            bom=("KM100BP", "BP108", "KB1X1", *BASE_BOM),
            default_kind=OpticKind.SPLITTER,
            optic_param_fields=BP108_FIELDS,
        ),
        UnitType(
            id="knife-edge",
            name="Knife-edge prism mirror (MRAK25)",
            category="Mirrors",
            description="MRAK25-P01 knife-edge right-angle prism mirror on a KM100B kinematic platform, on a "
            "pedestal base (PH2 + BE1 + CF125C), its edge over the post. Combines two beams arriving from "
            "opposite sides into neighbouring beams leaving along the component's angle.",
            mounting=Mounting.PEDESTAL,
            label_prefix="KE",
            bill_of_materials=("MRAK25-P01", "KM100B", *BASE_BOM),
            builder=build_knife_edge,
            fields=(FORK_ANGLE,),
        ),
        UnitType(
            id="iris",
            name="Iris (ID25)",
            category="Apertures",
            description="ID25 iris diaphragm (Ø1–25 mm) on a pedestal base (PH2 + BE1 + CF125C). "
            "Angle = the direction of the beam through it.",
            mounting=Mounting.PEDESTAL,
            label_prefix="I",
            bill_of_materials=("ID25", *BASE_BOM),
            builder=build_iris,
            fields=(*IRIS_FIELDS, FORK_ANGLE),
        ),
        UnitType(
            id="h-iris",
            name="Iris (ID25), hole-mounted",
            category="Apertures",
            description="ID25 iris diaphragm (Ø1–25 mm) on a PH2 screwed into a hole. "
            "Angle = the direction of the beam through it.",
            mounting=Mounting.HOLE,
            label_prefix="I",
            bill_of_materials=("ID25", "PH2", 'TR-series Ø1/2" post'),
            builder=build_iris,
            fields=IRIS_FIELDS,
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
            id="objective",
            name="Reflective objective (LMM40X)",
            category="Objectives",
            description="LMM40X-P01 40X reflective objective in an M1RMSS adapter, in a CP35 cage plate on a pedestal "
            "base (PH2 + BE1 + CF125C). Angle = the direction it looks (toward the focus).",
            mounting=Mounting.PEDESTAL,
            label_prefix="OBJ",
            bill_of_materials=("LMM40X-P01", "M1RMSS", "CP35", *BASE_BOM),
            builder=build_objective,
            fields=(*OBJECTIVE_FIELDS, FORK_ANGLE),
        ),
        UnitType(
            id="h-objective",
            name="Reflective objective (LMM40X), hole-mounted",
            category="Objectives",
            description="LMM40X-P01 40X reflective objective in an M1RMSS adapter, in a CP35 cage plate on a PH2 "
            "screwed into a hole. Angle = the direction it looks (toward the focus).",
            mounting=Mounting.HOLE,
            label_prefix="OBJ",
            bill_of_materials=("LMM40X-P01", "M1RMSS", "CP35", "PH2", 'TR-series Ø1/2" post'),
            builder=build_objective,
            fields=OBJECTIVE_FIELDS,
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
                    minimum=COLLIMATOR_MIN_DISTANCE / MM,
                    maximum=2000,
                    step=0.1,
                    decimals=2,
                    tooltip="Equal to the focal length for a collimated beam. 5 mm at least; the CP35 "
                    "slides along the cage rods, which limits the far end (see the notes below).",
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
        *(
            UnitType(
                id="beam-expander-mirrored" if mirrored else "beam-expander",
                name="Beam expander 2X, mirrored" if mirrored else "Beam expander 2X",
                category="Beam expanders",
                description=(
                    "Thorlabs BE02R reflective beam expander"
                    + (" mounted upside down, so its beam path folds the other way" if mirrored else "")
                    + ". The input beam meets mirror 1 at the component's position; angle = input beam direction."
                ),
                mounting=Mounting.FREE,
                label_prefix="BX",
                bill_of_materials=("BE02R",),
                builder=partial(build_beam_expander, mirrored=mirrored),
            )
            for mirrored in (False, True)
        ),
        UnitType(
            id="sample-stage",
            name="Sample stage (MAX311D)",
            category="Stages",
            description="MAX311D NanoMax 3-axis flexure stage screwed to the breadboard, with a sample on its "
            "platform. Angle = the direction the sample faces; its travel moves the sample ±2 mm.",
            mounting=Mounting.HOLE,
            label_prefix="ST",
            bill_of_materials=("MAX311D", "1/4\"-20 cap screws", "sample"),
            builder=build_sample_stage,
            fields=SAMPLE_STAGE_FIELDS,
            hole_anchor="Base centre",
        ),
        UnitType(
            id="tslot-rail",
            name='T-slot rail, 1/2" (80/20)',
            category="Enclosure",
            description='1/2" four-slot aluminium extrusion. Drag on the board to draw it from end to end. '
            "At beam height it blocks beams; lying on the table, beams pass over it.",
            mounting=Mounting.FREE,
            label_prefix="FR",
            bill_of_materials=('1/2" T-slot extrusion',),
            builder=build_tslot_rail,
            fields=(_length_field("rail", 12.0), ParamField("rail.run", "Height", "choice", "beam", choices=RAIL_RUNS)),
            length_key="rail.length_in",
            structural=True,
            preview_params=(("rail.length_in", 2.5),),
        ),
        UnitType(
            id="tslot-post",
            name='T-slot post, 1/2" (80/20)',
            category="Enclosure",
            description='1/2" four-slot aluminium extrusion standing upright, e.g. at an enclosure corner. '
            "It blocks beams.",
            mounting=Mounting.FREE,
            label_prefix="FP",
            bill_of_materials=('1/2" T-slot extrusion',),
            builder=build_tslot_post,
            structural=True,
        ),
        UnitType(
            id="board",
            name="Enclosure board",
            category="Enclosure",
            description="Opaque panel standing on edge, e.g. 1/8\" hardboard in the T-slots of the frame. "
            "Drag on the board to draw it from end to end. It blocks beams.",
            mounting=Mounting.FREE,
            label_prefix="BD",
            bill_of_materials=("Enclosure panel",),
            builder=build_board,
            fields=(
                _length_field("board", 12.0),
                ParamField(
                    "board.thickness_in", "Thickness", "float", P.BOARD_THICKNESS, unit="in", minimum=0.02,
                    maximum=1.0, step=0.0625, decimals=4, tooltip="1/8\" hardboard fits a 1/2\" T-slot",
                ),
            ),
            length_key="board.length_in",
            structural=True,
            preview_params=(("board.length_in", 1.25),),
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
            fields=(FORK_ANGLE,),
        ),
    ]
    return {u.id: u for u in units}


UNIT_TYPES: dict[str, UnitType] = _build_registry()
CATEGORY_ORDER = (
    "Sources", "Mirrors", "Beamsplitters", "Lenses", "Objectives", "Apertures", "Stages", "Beam expanders", "Bases",
    "Enclosure",
)


def get_unit_type(type_id: str) -> UnitType:
    try:
        return UNIT_TYPES[type_id]
    except KeyError as exc:
        raise KeyError(f"unknown unit type {type_id!r}") from exc


def new_component_params(type_id: str) -> dict:
    return get_unit_type(type_id).default_params()


def catalog_parts() -> list[PlacedPart]:
    """Every distinct part orientation the catalog produces: each unit in each
    orientation, with default parameters and with each on/off option flipped
    (e.g. a mount lifted off its KB1X1), plus the pedestal clamping fork."""
    seen: dict[tuple, PlacedPart] = {}
    for unit in UNIT_TYPES.values():
        variants = [unit.default_params()]
        for f in unit.fields:
            if f.kind == "bool":
                params = unit.default_params()
                set_param(params, f.key, not f.default)
                variants.append(params)
        for orientation in [name for name, _ in unit.orientations()] or [""]:
            for params in variants:
                build = unit.build(Component("catalog", unit.id, orientation=orientation, params=params))
                parts = build.parts + build.detached_parts
                if unit.mounting is Mounting.PEDESTAL:
                    parts += fork_parts(default_fork())
                for part in parts:
                    seen.setdefault((part.part_id, part.prims, part.roll, part.z_offset), part)
    return list(seen.values())
