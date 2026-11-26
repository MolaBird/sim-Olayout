"""Combined units: what the user actually places on the breadboard.

Each :class:`UnitType` knows its bill of materials, how it is fastened to
the table (:class:`Mounting`), its editable parameters (:class:`ParamField`)
and how to *build* itself: a :class:`UnitBuild` listing the placed parts,
optical elements, emitters and beam blockers in the unit frame.

Unit frame: origin on the post axis (table level), +x is the direction the
optic's front face points (for sources: the beam direction).
"""

from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass, replace
from enum import Enum
from functools import cached_property, partial
from typing import Any, Callable

import numpy as np
import shapely
from shapely.affinity import translate
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
from olayout.core.layout import Component, register_component_upgrade
from olayout.core.units import MM, NM, UM, normalize_angle
from olayout.optics.elements import Blocker, Emitter, OpticalElement, OpticKind


class Mounting(str, Enum):
    """How a component is fastened to the table.

    A unit that stands on a post is ``POST``: each component then has its own
    post holder, screwed into a hole (it behaves as ``HOLE``) or clamped on a
    pedestal (``PEDESTAL``); see :func:`mounting_of`.  A unit whose own base
    has slots (``SLOTS``) is screwed straight to the table through them, so it
    snaps to where its slots catch holes (see :class:`ArcSlot`).
    """

    HOLE = "hole"  # screwed straight into a hole: snaps to the holes
    PEDESTAL = "pedestal"  # PH2 + BE1 + clamping fork: anywhere; the fork screw needs a hole in reach
    FREE = "free"  # virtual objects (e.g. a beam source): no mechanical constraints
    POST = "post"  # on a post, in a holder that is in a hole or clamped (each component says which)
    SLOTS = "slots"  # screwed to the table through slots in its own base: snaps to where they catch holes


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
class ArcSlot:
    """A slot in a unit's own base for a 1/4"-20 screw into the table: an arc
    about the unit origin (so the unit can turn about it while screwed down)."""

    radius: float  # of the slot's centre line, inches
    middle: float  # direction of the arc's middle, degrees, unit frame
    half_span: float  # degrees the screw can sit either side of the middle
    width: float  # inches
    name: str = ""

    def catches(self, local: tuple[float, float], play: float) -> bool:
        """Whether a screw into a hole at ``local`` (unit frame) passes through
        this slot, ``play`` inches off its centre line at most."""
        r = math.hypot(*local)
        angle = math.degrees(math.atan2(local[1], local[0]))
        return abs(r - self.radius) <= play and abs((angle - self.middle + 180.0) % 360.0 - 180.0) <= self.half_span + 1e-9


@dataclass(frozen=True)
class ForkPlacement:
    """Resolved clamping-fork placement for a clamped post."""

    angle: float  # slot direction, degrees, unit frame
    screw_distance: float  # pedestal centre to screw axis
    hole: tuple[float, float] | None = None  # world hole used (None: no hole in reach)
    clamp: str = P.DEFAULT_CLAMP  # which fork (see parts.CLAMPS)


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
    """A placeable unit.  Its build never includes a clamped post's fork:
    the fork swivels independently (see :func:`fork_parts`), so moving a
    clamped post never forces a rebuild."""

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
    slots: tuple[ArcSlot, ...] = ()  # a ``SLOTS`` unit: the slots in its base

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


PELLICLE_MOUNTS = P.PELLICLE_MOUNTS  # mount id -> the pellicle ring it holds
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


THUMBSCREW_ANGLE = 180.0  # by default the PH2 thumbscrew points to the unit's -y side
# A post holder (in a hole or on a pedestal) can be turned so its thumbscrew
# faces any side; looking along the component's angle, "right" is the unit's -y side.
THUMBSCREW_SIDES = (("right", "Right"), ("left", "Left"), ("back", "Back"), ("front", "Front"))
_THUMBSCREW_TURN = {"right": THUMBSCREW_ANGLE, "left": 0.0, "back": 90.0, "front": -90.0}
THUMBSCREW_FIELD = ParamField(
    "post.thumbscrew",
    "Thumbscrew",
    "choice",
    "right",
    choices=THUMBSCREW_SIDES,
    tooltip="Which side of the post holder its thumbscrew faces, looking along the component's angle",
)


def _thumbscrew(comp: Component | None) -> str:
    side = get_param(comp.params, THUMBSCREW_FIELD.key, "right") if comp is not None else "right"
    return side if side in _THUMBSCREW_TURN else "right"


def _post_holder_parts(on_pedestal: bool, thumbscrew: str = "right") -> list[PlacedPart]:
    turn = _THUMBSCREW_TURN.get(thumbscrew, THUMBSCREW_ANGLE)
    return [_placed("PH2@BE1" if on_pedestal else "PH2", Pose(0, 0, turn))]


def _base_parts(clamped: bool, thumbscrew: str = "right") -> list[PlacedPart]:
    """The post holder, on a BE1 pedestal when clamped.  The pedestal's clamping
    fork is not part of the build: it is placed per position (:func:`fork_parts`)."""
    return [*([_placed("BE1")] if clamped else []), *_post_holder_parts(clamped, thumbscrew)]


def default_fork(clamp: str = P.DEFAULT_CLAMP) -> ForkPlacement:
    spec = P.clamp_spec(clamp)
    lo, hi = spec.screw_travel
    return ForkPlacement(angle=180.0, screw_distance=(lo + hi) / 2, clamp=spec.part_id)


def fork_parts(fork: ForkPlacement) -> tuple[PlacedPart, ...]:
    """The clamping fork and its cap screw for a resolved fork placement (unit frame)."""
    rad = math.radians(fork.angle)
    screw = Pose(fork.screw_distance * math.cos(rad), fork.screw_distance * math.sin(rad), 0.0)
    return (_placed(P.clamp_spec(fork.clamp).part_id, Pose(0, 0, fork.angle)), _placed("SCREW", screw))


_CLAMP_SHAPES: dict[str, object] = {}


def clamp_footprint(clamp: str):
    """A clamping fork's footprint in its own frame: the slot along +x, the
    pedestal centre at the origin (the screw head lies within the fork)."""
    part_id = P.clamp_spec(clamp).part_id
    if part_id not in _CLAMP_SHAPES:
        _CLAMP_SHAPES[part_id] = get_part(part_id).footprint()
    return _CLAMP_SHAPES[part_id]


def fork_footprint(fork: ForkPlacement):
    """Fork footprint in the unit frame."""
    return transform_geometry(clamp_footprint(fork.clamp), Pose(0.0, 0.0, fork.angle))


def slot_screw_parts(pose: Pose, holes) -> tuple[PlacedPart, ...]:
    """Cap screws in the world holes a slotted base is screwed down to (unit frame)."""
    rad = math.radians(-pose.angle)
    c, s = math.cos(rad), math.sin(rad)
    screws = []
    for hole in holes:
        if hole is not None:
            dx, dy = hole[0] - pose.x, hole[1] - pose.y
            screws.append(_placed("SCREW", Pose(c * dx - s * dy, s * dx + c * dy, 0.0)))
    return tuple(screws)


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
# Posts.  Every post-mounted unit stands on a Ø1/2" post in a PH2 post
# holder.  The holder is either screwed into a hole, or stands on a BE1
# pedestal held down by a clamping fork (which finds a hole in reach by
# itself).  A KB1X1 magnetic base can sit between the post and the mount, so
# the mount can be lifted off and put back repeatably.  These choices belong
# to the component, not its type: they are its post parameters, and the mount
# on top can be swapped (or taken off, leaving a bare post) without touching
# them.
BARE_POST = "post"  # the unit type of a post with nothing on it
DEFAULT_TAP_Z = -1.0  # top of a bare post: a KB1X1 on it sits there
BASE_KINDS = (("hole", "In a hole"), ("clamped", "Clamped (BE1 + fork)"))
BASE_KIND_FIELD = ParamField(
    "base.kind", "Post holder", "choice", "hole", choices=BASE_KINDS,
    tooltip="A PH2 screwed straight into a hole, or a PH2 on a BE1 pedestal that a clamping fork holds down "
    "anywhere (right-click to switch)",
)
CLAMP_FIELD = ParamField(
    "base.clamp", "Clamp", "choice", P.DEFAULT_CLAMP,
    choices=tuple((c.part_id, c.label) for c in P.CLAMPS.values()),
    visible_when=("base.kind", ("clamped",)),
    tooltip="The short CF038 fits where a CF125C would hit a neighbour, but reaches fewer holes",
)
CLAMP_ANGLE_FIELD = ParamField(
    "clamp.angle", "Clamp direction", "float", None, unit="°", minimum=-360, maximum=360, step=5, decimals=1,
    visible_when=("base.kind", ("clamped",)),
    tooltip="Where the clamp points on the table (0° = +x).  Auto picks a reachable hole clear of the "
    "neighbours; a set direction takes the reachable hole nearest to it (right-click: Rotate Clamp).",
)
KB1X1_PRESENT = ParamField(
    "kb1x1.present", "KB1X1 base", "bool", False,
    tooltip="A KB1X1 magnetic base between the post and the mount: the mount can be lifted off and put back (K)",
)
KB1X1_SEATED = ParamField(
    "kb1x1.seated", "Mount on its KB1X1", "bool", True,
    visible_when=("kb1x1.present", (True,)),
    tooltip="Untick (or press K) to lift the mount off its KB1X1 magnetic base: it stops "
    "reflecting, blocking and colliding until you put it back.",
)
POST_FIELDS = (BASE_KIND_FIELD, CLAMP_FIELD, CLAMP_ANGLE_FIELD, THUMBSCREW_FIELD, KB1X1_PRESENT, KB1X1_SEATED)
POST_KEYS = ("base", "clamp", "post", "kb1x1")  # parameter groups that belong to the post, not the mount
LIFTED_NOTE = "Lifted off its KB1X1: no optics or collisions until it is put back (K)"


def stands_on_post(type_id: str) -> bool:
    unit = UNIT_TYPES.get(type_id)
    return unit is not None and unit.mounting is Mounting.POST


def is_clamped(comp: Component) -> bool:
    return get_param(comp.params, BASE_KIND_FIELD.key, "hole") == "clamped"


def clamp_of(comp: Component) -> str:
    return P.clamp_spec(get_param(comp.params, CLAMP_FIELD.key, P.DEFAULT_CLAMP)).part_id


def clamp_angle(comp: Component) -> float | None:
    """The clamp direction the user set (degrees on the table), or None: automatic."""
    value = get_param(comp.params, CLAMP_ANGLE_FIELD.key, None)
    try:
        value = None if value is None else float(value)
    except (TypeError, ValueError):
        return None
    return value if value is None or math.isfinite(value) else None


def mounting_of(comp: Component) -> Mounting:
    """How this component is fastened: a post-mounted one as its own holder
    says (``HOLE`` or ``PEDESTAL``), any other as its type does."""
    unit = UNIT_TYPES.get(comp.type)
    if unit is None:
        return Mounting.FREE
    if unit.mounting is Mounting.POST:
        return Mounting.PEDESTAL if is_clamped(comp) else Mounting.HOLE
    return unit.mounting


def has_kb1x1(comp: Component) -> bool:
    return stands_on_post(comp.type) and bool(get_param(comp.params, KB1X1_PRESENT.key, False))


def has_mount(comp: Component) -> bool:
    """Whether something stands on this component's post."""
    return stands_on_post(comp.type) and comp.type != BARE_POST


def lifted_off(comp: Component) -> bool:
    """Whether the mount is off its KB1X1 (only a post with a KB1X1 can be)."""
    return has_kb1x1(comp) and not bool(get_param(comp.params, KB1X1_SEATED.key, True))


@dataclass(frozen=True)
class Top:
    """What a post-mounted unit puts on its post, in its unit frame (post axis
    at the origin, beam plane at z = 0)."""

    parts: tuple[PlacedPart, ...] = ()  # the mount and what it holds; it comes off with a KB1X1's top plate
    tap_z: float = DEFAULT_TAP_Z  # height of the face that sits on the post: a KB1X1 goes right under it
    elements: tuple[OpticalElement, ...] = ()
    emitters: tuple[Emitter, ...] = ()
    blockers: tuple[Blocker, ...] = ()  # besides the parts' own cross-sections (substrates, blades, glass)
    see_through: frozenset[str] = frozenset()  # parts whose cross-section is drawn by the optics instead
    optic_visuals: tuple[OpticVisual, ...] = ()
    notes: tuple[str, ...] = ()


def build_on_post(unit: UnitType, comp: Component, *, top: Callable[[UnitType, Component], Top]) -> UnitBuild:
    """A post-mounted unit: its holder (in a hole or clamped), the KB1X1 if
    any, and the mount on top."""
    mount = top(unit, comp)
    base = _base_parts(is_clamped(comp), _thumbscrew(comp))
    if has_kb1x1(comp):
        if lifted_off(comp):
            base.append(_placed("KB1X1-BOTTOM", Pose(), z_offset=mount.tap_z))
            return UnitBuild(
                parts=tuple(base),
                blockers=tuple(_beam_blockers(base)),
                notes=(LIFTED_NOTE,) if mount.parts else (),
                detached_parts=mount.parts,
            )
        base.append(_placed("KB1X1", Pose(), z_offset=mount.tap_z))
    solid = [p for p in mount.parts if p.part_id not in mount.see_through]
    return UnitBuild(
        parts=(*base, *mount.parts),
        elements=mount.elements,
        emitters=mount.emitters,
        blockers=(*_beam_blockers(base), *_beam_blockers(solid), *mount.blockers),
        optic_visuals=mount.optic_visuals,
        notes=mount.notes,
    )


def _tap_z(part_id: str, tap: str | None = None) -> float:
    return get_part(part_id).oriented(tap).tap_z


def bare_post_top(unit: UnitType, comp: Component) -> Top:
    return Top(notes=("Nothing on it: drop a mount from the library onto it",))


def mount_top(unit: UnitType, comp: Component, *, mount_id: str, default_kind: OpticKind) -> Top:
    """A mount holding one optic, its chosen tap on the post."""
    mount, mount_pose = _mount_on_post(mount_id, comp.orientation or None)
    parts = [mount]
    if mount_id in PELLICLE_MOUNTS and _optic_kind(comp.params, default_kind) is not OpticKind.NONE:
        # The pellicle's own ring sits in the mount, its membrane on the optic slot's plane.
        membrane = Pose(get_part(mount_id).optic_slot.seat_x, 0.0, 0.0)
        parts.append(_placed(PELLICLE_MOUNTS[mount_id].part_id, mount_pose.compose(membrane)))
    elements, substrate, visuals = _optic_elements(
        get_part(mount_id), mount_pose, comp.params, default_kind, comp.label
    )
    return Top(
        parts=tuple(parts),
        tap_z=_tap_z(mount_id, comp.orientation or None),
        elements=tuple(elements),
        blockers=tuple(substrate),
        optic_visuals=tuple(visuals),
    )


def build_beam_expander(unit: UnitType, comp: Component, *, mirrored: bool) -> UnitBuild:
    """BE02R: the beam travels along +x and meets mirror 1 at the unit origin.

    The mirrored version is the same expander upside down, so its beam path
    folds the other way (y -> -y).
    """
    flip = -1.0 if mirrored else 1.0
    parts = [_placed("BE02R", Pose(), roll=rotation_matrix("x", 180) if mirrored else None)]
    elements, visuals = _be02r_optics(comp.label, flip)
    side = "right" if mirrored else "left"
    notes = (*_be02r_notes()[:1], f"The beam leaves {P.BE02R_EXIT_ANGLE:.1f}° to the {side} of its input direction",
             *_be02r_notes()[1:])
    return UnitBuild(
        parts=tuple(parts),
        elements=tuple(elements),
        blockers=tuple(_beam_blockers(parts)),
        optic_visuals=tuple(visuals),
        notes=notes,
    )


def _be02r_optics(label: str, flip: float) -> tuple[list[OpticalElement], list[OpticVisual]]:
    """The BE02R's two mirrors (``flip`` = -1: the expander upside down)."""
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
                name=f"{label} mirror {index}".strip(),
            )
        )
        tangent = math.radians(angle + 90.0)
        centre = Pose(x + offset * math.cos(tangent), y * flip + offset * math.sin(tangent), angle)
        visuals.append(OpticVisual(OpticKind.MIRROR, centre, hi - lo, mirror.thickness, 1 if mirror.roc > 0 else -1))
    return elements, visuals


def _be02r_notes() -> tuple[str, str]:
    first, second = P.BE02R_MIRRORS
    edge = min(-first.aperture[0], first.aperture[1])  # mirror 1's edge nearest the beam axis
    across = edge * abs(math.cos(math.radians(first.normal_angle)))  # as the obliquely incident beam sees it
    return (
        f"2X: convex R {-first.roc / MM:.1f} mm, then concave R {second.roc / MM:.0f} mm",
        # A Gaussian loses 1% past an edge 1.645 w / √2 from its centre.
        f"Mirror 1's edge is {edge / MM:.1f} mm from the axis: beams over ~Ø{2 * math.sqrt(2) * across / 1.645 / MM:.1f} mm clip",
    )


def build_beam_expander_riser(unit: UnitType, comp: Component) -> UnitBuild:
    """BE02R (upright) on its custom riser: the same optics as the free-standing
    expander, but its position is fixed by the riser's two bolts in table holes."""
    parts = [_placed("BE02R-RISER"), _placed("BE02R-RISER-WALL"), _placed("BE02R")]
    elements, visuals = _be02r_optics(comp.label, 1.0)
    notes = (
        f"The beam leaves {P.BE02R_EXIT_ANGLE:.1f}° to the left of its input direction",
        *_be02r_notes(),
        "Custom riser: height = beam height - 25.0 mm; drawing in docs/custom-parts.md",
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


def collimator_top(unit: UnitType, comp: Component) -> Top:
    params = comp.params
    sm1zb_part = get_part("SM1ZB")
    tap = sm1zb_part.tap(comp.orientation or None)
    rod_length = _num(params, "rods.length_in", DEFAULT_ROD_LENGTH, *ROD_LENGTH_RANGE)
    d_min, d_max = collimator_lens_range(tap.name, rod_length)
    f_mm = _num(params, "lens.focal_length_mm", 25.4, -1e4, 1e4)
    wanted = _num(params, "lens.distance_mm", 25.4, 0.0, 2000.0) * MM
    distance = min(max(wanted, d_min), d_max)

    sm1zb, frame = _mount_on_post("SM1ZB", tap.name)  # the post sits right under the optic axis
    parts: list[PlacedPart] = [sm1zb]
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
    return Top(
        parts=tuple(parts),
        tap_z=_tap_z("SM1ZB", tap.name),
        elements=tuple(elements),
        emitters=emitters,
        notes=tuple(notes),
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


def objective_top(unit: UnitType, comp: Component) -> Top:
    """CP35 on the post (its mid-plane over the post axis), the M1RMSS ring
    threaded in flush with its front face, and the LMM40X screwed in until its
    shoulder seats on the ring, pointing along +x toward the sample."""
    params = comp.params
    plate_front = P.CP35_THICKNESS / 2
    parts = (
        _placed("CP35", Pose(plate_front, 0.0, 0.0), roll=CP35_SPRITE_ROLL),
        _placed("M1RMSS", Pose(plate_front, 0.0, 0.0)),
        _placed("LMM40X", Pose(plate_front, 0.0, 0.0)),
    )
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
    return Top(parts=parts, tap_z=_tap_z("CP35"), elements=(element,), notes=notes)


# Knife-edge combiner = MRAK25-P01 prism mirror on a KM100B platform on a
# post, its knife edge over the post.  Each silvered leg is a flat mirror
# reaching right up to the edge, so two beams arriving square to the output
# from opposite sides leave side by side, as close as their own sizes allow.
# A beam's centre should stay 1.5 w from the edge (1.3 w loses 0.5%).
def _mrak25_glass(lead: float) -> Blocker:
    """The glass behind the silvered legs, kept just behind them so the legs themselves reflect."""
    return Blocker(translate(Polygon(P.mrak25_outline()).buffer(-0.003, join_style="mitre"), lead, 0.0))


def knife_edge_top(unit: UnitType, comp: Component) -> Top:
    elements, visuals = _knife_edge_legs(comp.label, 0.0)
    return Top(
        parts=(_placed("KM100B"), _placed("MRAK25")),
        tap_z=_tap_z("KM100B"),
        elements=tuple(elements),
        blockers=(_mrak25_glass(0.0),),
        see_through=frozenset({"MRAK25"}),
        optic_visuals=tuple(visuals),
        notes=(
            "Beams arriving square to the angle from either side leave along it, side by side",
            "Keep each beam's centre 1.5 w from the edge (at 1.3 w it loses 0.5%)",
            "MRAK25 and KM100B are drawn from catalog dimensions (no vendor CAD)",
        ),
    )


def _knife_edge_legs(label: str, lead: float) -> tuple[list[OpticalElement], list[OpticVisual]]:
    """The MRAK25's two silvered legs, its edge at x = ``lead`` pointing +x."""
    reach = P.MRAK25_LEG / math.sqrt(2)  # each leg runs back this far, and as far to its side
    elements, visuals = [], []
    for side, sign in (("left", 1.0), ("right", -1.0)):  # as seen looking along the output
        centre = (lead - reach / 2, sign * reach / 2)
        normal = 45.0 * sign  # each leg turns a beam coming from its side toward +x
        elements.append(
            OpticalElement(
                kind=OpticKind.MIRROR,
                center=centre,
                normal_angle=normal,
                half_aperture=P.MRAK25_LEG / 2,
                name=f"{label} {side} leg".strip(),
            )
        )
        visuals.append(OpticVisual(OpticKind.MIRROR, Pose(*centre, normal), P.MRAK25_LEG, 0.012))
    return elements, visuals


def knife_edge_holder_top(unit: UnitType, comp: Component) -> Top:
    """MRAK25 in the custom holder.  The edge points +x, KNIFE_EDGE_LEAD ahead of
    the post, so beams fed along the post's own column leave 2 x KNIFE_EDGE_LEAD
    (9.0 mm) apart."""
    lead = P.KNIFE_EDGE_LEAD
    elements, visuals = _knife_edge_legs(comp.label, lead)
    return Top(
        parts=(_placed("KEH"), _placed("MRAK25", Pose(lead, 0.0, 0.0))),
        tap_z=_tap_z("KEH"),
        elements=tuple(elements),
        blockers=(_mrak25_glass(lead),),
        see_through=frozenset({"MRAK25"}),
        optic_visuals=tuple(visuals),
        notes=(
            "Feed both beams along the post's column, one from each side: they leave 9.0 mm apart",
            f"The edge is {P.KNIFE_EDGE_LEAD / MM:.1f} mm ahead of the post; each beam {P.KNIFE_EDGE_LEAD / MM:.1f} mm from it",
            "Custom holder: drawing in docs/custom-parts.md (MRAK25 drawn from catalog dimensions)",
        ),
    )


def beam_dump_top(unit: UnitType, comp: Component, *, part_id: str = "LB1") -> Top:
    """A beam dump or block on the post; it faces the unit's angle."""
    note = ('Absorbs whatever enters its 1.4" x 0.7" aperture; face it toward the beam' if part_id == "LB1"
            else "Bead-blasted, black-anodized block: face it toward the stray beam (or turn it to clear a "
            "neighbour); it scatters what it does not absorb")
    return Top(parts=(_placed(part_id),), tap_z=_tap_z(part_id), notes=(note,))


TARGET_FIELDS = (
    ParamField(
        "target.spacing_mm", "Pinhole spacing", "float", 0.0, unit="mm", minimum=0.0, maximum=30.0, step=0.1,
        decimals=2, tooltip="0: one centred pinhole; otherwise two, this far apart, for two beams side by side",
    ),
    ParamField(
        "target.hole_mm", "Pinhole Ø", "float", 1.0, unit="mm", minimum=0.3, maximum=6.0, step=0.1, decimals=2,
        tooltip="About the beam's 1/e² radius: a centred beam shows a symmetric ring behind it",
    ),
)


def target_top(unit: UnitType, comp: Component) -> Top:
    """Pinhole target: the pinholes pass the beams that are on them, the plate
    stops everything else.  Angle = the beams' direction."""
    spacing = _num(comp.params, "target.spacing_mm", 0.0, 0.0, 30.0) * MM
    half = _num(comp.params, "target.hole_mm", 1.0, 0.3, 6.0) * MM / 2
    centres = (0.0,) if spacing <= 0 else (spacing / 2, -spacing / 2)
    elements = [
        OpticalElement(kind=OpticKind.WINDOW, center=(0.0, y), normal_angle=0.0, half_aperture=half,
                       name=f"{comp.label} pinhole{'' if len(centres) == 1 else ' ' + ('left' if y > 0 else 'right')}")
        for y in centres
    ]
    (_, y0, _), (_, y1, _) = P.TARGET_PLATE
    edges = sorted([y0, y1, *(c - half for c in centres), *(c + half for c in centres)])
    plate = [Blocker(box(-0.01, lo, 0.01, hi)) for lo, hi in zip(edges[0::2], edges[1::2]) if hi - lo > 1e-6]
    return Top(
        parts=(_placed("TARGET"),),
        tap_z=_tap_z("TARGET"),
        elements=tuple(elements),
        blockers=tuple(plate),
        see_through=frozenset({"TARGET"}),
        notes=("Each beam should pass its pinhole: a centred beam shows a round, symmetric spot behind it",),
    )


# Iris = ID25 on a post.  The opening passes beams (clipping the ones it is
# too small for); the blades and the housing stop everything else.
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


def iris_top(unit: UnitType, comp: Component) -> Top:
    aperture = _num(comp.params, "iris.aperture_mm", 3.0, *P.ID25_APERTURE_MM) * MM
    half, bore = aperture / 2, P.ID25_OPENING
    element = OpticalElement(
        kind=OpticKind.WINDOW, center=(0.0, 0.0), normal_angle=0.0, half_aperture=half, name=f"{comp.label} iris".strip()
    )
    blades = [Blocker(box(-0.01, lo, 0.01, hi)) for lo, hi in ((half, bore), (-bore, -half)) if hi - lo > 1e-6]
    return Top(
        parts=(_placed("ID25"),),
        tap_z=_tap_z("ID25"),
        elements=(element,),
        blockers=tuple(blades),
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


# Optical chopper = MC2000B head, standing on the table with no post: its base
# plate is screwed down through its own two arc slots.  The blade is a vertical
# disc across the beam; where the beam crosses it depends on how high the beam
# is above the table, which a post would otherwise absorb.  Each slotted ring
# passes the beam half the time (shown as 50% transmission, the time average);
# the hub, the rings between slot rows and the rim stop it; outside the blade
# it passes freely.
CHOPPER_SLOTS = tuple(
    ArcSlot(P.MC2000B_SLOT_RADIUS, middle, P.MC2000B_SLOT_HALF_SPAN, P.MC2000B_SLOT_WIDTH, name)
    for middle, name in ((90.0, "left"), (-90.0, "right"))
)
CHOPPER_FIELDS = (
    ParamField(
        "chopper.beam_height_in", "Beam height", "float", 3.5, unit="in", minimum=1.0, maximum=6.0, step=0.125,
        decimals=3,
        tooltip="Beam height above the table. The head stands on the table, so this sets where the beam crosses "
        f"the blade (its hub is {P.MC2000B_HUB[2]:.3f}\" up).",
    ),
)


def build_chopper(unit: UnitType, comp: Component) -> UnitBuild:
    height = _num(comp.params, "chopper.beam_height_in", 3.5, 1.0, 6.0)
    head = _placed("MC2000B", z_offset=-height)
    blade_x, _, hub_z = P.MC2000B_HUB
    above = height - hub_z  # the beam's height above the hub
    body = transform_all(P.mc2000b_body_prims(), np.eye(3), (0.0, 0.0, -height))
    blockers = [Blocker(section)] if not (section := slice_at(body, 0.0)).is_empty else []
    elements: list[OpticalElement] = []
    crossings = []
    for r0_mm, r1_mm, open_fraction, ring in P.MC2000B_BLADE_RINGS_MM:
        r0, r1 = r0_mm * MM, r1_mm * MM
        if r1 <= abs(above):
            continue  # the beam passes above (or below) this ring
        u0, u1 = math.sqrt(max(r0**2 - above**2, 0.0)), math.sqrt(r1**2 - above**2)
        bands = [(-u1, u1)] if r0 <= abs(above) else [(u0, u1), (-u1, -u0)]  # along the blade, either side of the hub
        crossings.append((u0, u1, open_fraction, ring))
        for lo, hi in bands:
            if open_fraction > 0:
                side = "" if len(bands) == 1 else (" left" if lo > 0 else " right")
                elements.append(OpticalElement(
                    kind=OpticKind.WINDOW, center=(blade_x, (lo + hi) / 2), normal_angle=0.0, half_aperture=(hi - lo) / 2,
                    transmittance=open_fraction, name=f"{comp.label} {ring}{side}".strip(),
                ))
            else:
                blockers.append(Blocker(box(blade_x - 0.005, lo, blade_x + 0.005, hi)))
    elements.sort(key=lambda e: (abs(e.center[1]), -e.center[1]))  # the slots nearest the hub first
    slotted = [f"{ring} {u0:.2f}–{u1:.2f}\"" for u0, u1, fraction, ring in crossings if fraction > 0]
    notes = (
        f"The beam {height:.3f}\" up crosses the blade {abs(above):.3f}\" {'above' if above >= 0 else 'below'} the hub; "
        "it is chopped (50%) where it passes " + (", ".join(slotted) if slotted else "no slots") + " from the hub, "
        "either side",
        f"Screw it down through both base slots: centred midway between two holes {2 * P.MC2000B_SLOT_RADIUS:g}\" "
        f"apart, turned up to {P.MC2000B_SLOT_HALF_SPAN:g}° either way",
    )
    return UnitBuild(
        parts=(head,),
        elements=tuple(elements),
        blockers=tuple(blockers),
        keepouts=((Layer.BASE, head.footprint()),),
        notes=notes,
    )


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
def _post_unit(
    uid: str,
    name: str,
    *,
    category: str,
    description: str,
    prefix: str,
    bom: tuple[str, ...],
    top: Callable[[UnitType, Component], Top],
    fields: tuple[ParamField, ...] = (),
    orientation_part: str | None = None,
) -> UnitType:
    """A unit that stands on a post; the post's own parameters come after its fields."""
    return UnitType(
        id=uid,
        name=name,
        category=category,
        description=description,
        mounting=Mounting.POST,
        label_prefix=prefix,
        bill_of_materials=bom,
        builder=partial(build_on_post, top=top),
        orientation_part=orientation_part,
        fields=(*fields, *POST_FIELDS),
    )


def _mount_unit(
    uid: str,
    name: str,
    *,
    category: str,
    description: str,
    prefix: str,
    mount_id: str,
    bom: tuple[str, ...],
    default_kind: OpticKind,
    kinds: tuple[OpticKind, ...] | None = None,
    optic_param_fields: tuple[ParamField, ...] | None = None,
) -> UnitType:
    if optic_param_fields is not None:
        fields = tuple(optic_param_fields)
    else:
        fields = optic_fields(default_kind, kinds, with_thickness=mount_id not in PELLICLE_MOUNTS)
    return _post_unit(
        uid,
        name,
        category=category,
        description=description,
        prefix=prefix,
        bom=bom,
        top=partial(mount_top, mount_id=mount_id, default_kind=default_kind),
        fields=fields,
        orientation_part=mount_id,
    )


ON_A_POST = "on a post: in a hole, or clamped on a pedestal (right-click to switch)"
HOLDER_BOM = ("PH2", 'TR-series Ø1/2" post')


def _pellicle_fields(part_id: str) -> tuple[ParamField, ...]:
    """A pellicle mount's optic is always its pellicle; only the split ratio is
    editable (the other BP1xx / BP2xx coatings share the ring)."""
    return (
        ParamField(
            "optic.reflectance",
            "Reflectance R",
            "float",
            P.BP108_REFLECTANCE,
            minimum=0.0,
            maximum=1.0,
            step=0.01,
            decimals=3,
            tooltip=f"{part_id}: 8:92 (R:T), uncoated, 400–2400 nm",
        ),
    )


# The pellicle beamsplitters: (unit id, mount, size label, kinematic).
_PELLICLE_UNITS = (
    ("pellicle-km100bp", "KM100BP", 'Ø1"', True),
    ("pellicle-bp107", "BP107", 'Ø1"', False),
    ("pellicle-km200bp", "KM200BP", 'Ø2"', True),
    ("pellicle-bp207", "BP207", 'Ø2"', False),
)


def _pellicle_units() -> list[UnitType]:
    units = []
    for uid, mount_id, size, kinematic in _PELLICLE_UNITS:
        ring = PELLICLE_MOUNTS[mount_id].part_id
        holder = f"a {mount_id} kinematic mount" if kinematic else f"a {mount_id} fixed mount"
        units.append(
            _mount_unit(
                uid,
                f"{'Kinematic' if kinematic else 'Fixed'} pellicle beamsplitter, {size} ({ring})",
                category="Beamsplitters",
                description=f"{ring} 8:92 pellicle in {holder}, {ON_A_POST}. Put a KB1X1 on the post first to lift "
                "it off and put it back with K.",
                prefix="BS",
                mount_id=mount_id,
                bom=(mount_id, ring),
                default_kind=OpticKind.SPLITTER,
                optic_param_fields=_pellicle_fields(ring),
            )
        )
    return units


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
        _post_unit(
            BARE_POST,
            "Post",
            category="Posts",
            description='Ø1/2" post in a PH2 post holder, nothing on it yet. Drop a mount (or a KB1X1) from the '
            "library onto it. Right-click to clamp it on a pedestal instead of screwing it into a hole.",
            prefix="PH",
            bom=(),
            top=bare_post_top,
        ),
        _mount_unit(
            "km100",
            "Kinematic mirror (KM100)",
            category="Mirrors",
            description=f"KM100 kinematic mount for Ø1\" optics, {ON_A_POST}.",
            prefix="M",
            mount_id="KM100",
            bom=("KM100",),
            default_kind=OpticKind.MIRROR,
        ),
        _mount_unit(
            "km100cp",
            "Kinematic mirror, post-centred (KM100CP)",
            category="Mirrors",
            description=f"KM100CP kinematic mount whose taps sit right under the mirror surface, {ON_A_POST}.",
            prefix="M",
            mount_id="KM100CP",
            bom=("KM100CP",),
            default_kind=OpticKind.MIRROR,
        ),
        _mount_unit(
            "kmsr",
            "Kinematic square mirror (KMSR)",
            category="Mirrors",
            description='PFSQ10-03-P01 1" x 1" protected silver mirror (6 mm thick) in a KMSR compact kinematic '
            f"mount, {ON_A_POST}.",
            prefix="M",
            mount_id="KMSR",
            bom=("KMSR", "PFSQ10-03-P01"),
            default_kind=OpticKind.MIRROR,
        ),
        _mount_unit(
            "fmp1",
            "Fixed mirror (FMP1)",
            category="Mirrors",
            description=f"FMP1 fixed mount for Ø1\" optics, {ON_A_POST}. With a KB1X1 under it, it lifts off "
            "and goes back repeatably (K).",
            prefix="M",
            mount_id="FMP1",
            bom=("FMP1",),
            default_kind=OpticKind.MIRROR,
        ),
        _post_unit(
            "knife-edge",
            "Knife-edge prism mirror (MRAK25)",
            category="Mirrors",
            description="MRAK25-P01 knife-edge right-angle prism mirror on a KM100B kinematic platform, its edge over "
            f"the post, {ON_A_POST}. Combines two beams arriving from opposite sides into neighbouring beams "
            "leaving along the component's angle.",
            prefix="KE",
            bom=("MRAK25-P01", "KM100B"),
            top=knife_edge_top,
        ),
        _post_unit(
            "knife-edge-post",
            "Knife-edge prism mirror (MRAK25), custom holder",
            category="Mirrors",
            description="MRAK25-P01 knife-edge prism mirror in a machined holder on a post. The edge sits 4.5 mm "
            "ahead of the post, so two beams fed along the post's column from opposite sides leave side by side, "
            "9.0 mm apart, along the component's angle.",
            prefix="KE",
            bom=("MRAK25-P01", "Custom knife-edge holder (docs/custom-parts.md)"),
            top=knife_edge_holder_top,
        ),
        *_pellicle_units(),
        _mount_unit(
            "lens",
            "Lens (FMP1)",
            category="Lenses",
            description=f"Ø1\" lens in an FMP1, {ON_A_POST} (any FMP1 can hold a lens).",
            prefix="L",
            mount_id="FMP1",
            bom=("FMP1",),
            default_kind=OpticKind.LENS,
        ),
        _post_unit(
            "objective",
            "Reflective objective (LMM40X)",
            category="Objectives",
            description="LMM40X-P01 40X reflective objective in an M1RMSS adapter, in a CP35 cage plate, "
            f"{ON_A_POST}. Angle = the direction it looks (toward the focus).",
            prefix="OBJ",
            bom=("LMM40X-P01", "M1RMSS", "CP35"),
            top=objective_top,
            fields=OBJECTIVE_FIELDS,
        ),
        _post_unit(
            "iris",
            "Iris (ID25)",
            category="Apertures",
            description=f"ID25 iris diaphragm (Ø1–25 mm), {ON_A_POST}. Angle = the direction of the beam through it.",
            prefix="I",
            bom=("ID25",),
            top=iris_top,
            fields=IRIS_FIELDS,
        ),
        _post_unit(
            "pinhole-target",
            "Pinhole alignment target (custom)",
            category="Apertures",
            description="Machined plate with one or two pinholes on a post: a fixed checkpoint the beams must pass. "
            "Angle = the beams' direction.",
            prefix="T",
            bom=("Custom pinhole target (docs/custom-parts.md)",),
            top=target_top,
            fields=TARGET_FIELDS,
        ),
        _post_unit(
            "beam-dump",
            "Beam dump (LB1)",
            category="Beam dumps",
            description='Thorlabs LB1 beam dump on its Ø1/2" post. Its 1.4" x 0.7" aperture faces the component\'s '
            "angle.",
            prefix="BB",
            bom=("LB1",),
            top=beam_dump_top,
        ),
        _post_unit(
            "beam-block",
            "Beam block (custom)",
            category="Beam dumps",
            description='Black-anodized aluminium block, 1" x 1" x 3/8", on a post, for milliwatt stray beams where '
            "an LB1 does not fit. It faces the component's angle.",
            prefix="BB",
            bom=("Custom beam block (docs/custom-parts.md)",),
            top=partial(beam_dump_top, part_id="BLOCK"),
        ),
        _post_unit(
            "collimator",
            "Fiber collimator",
            category="Sources",
            description="SM1ZB fiber z-translator hanging from its post, four ER cage rods (ER1.5 by default), "
            f"CP35 cage plate with a Ø1\" lens; {ON_A_POST}.",
            prefix="FC",
            bom=("SM1ZB", "ER rods ×4", "CP35", "Ø1\" lens", "SM1 fiber adapter"),
            top=collimator_top,
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
            id="beam-expander-riser",
            name="Beam expander 2X on a custom riser",
            category="Beam expanders",
            description="Thorlabs BE02R bolted to a machined riser that puts its axis at beam height; the riser's "
            "two 1/4\"-20 bolts fix its position on the table. The input beam meets mirror 1 at the component's "
            "position; angle = input beam direction. Keep mirror 1 on a whole-inch x and the input on a hole row "
            "so both bolts land on holes.",
            mounting=Mounting.FREE,
            label_prefix="BX",
            bill_of_materials=("BE02R", "Custom riser (docs/custom-parts.md)", '1/4"-20 SHCS x2', "8-32 SHCS x4"),
            builder=build_beam_expander_riser,
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
            id="chopper",
            name="Optical chopper head (MC2000B)",
            category="Modulators",
            description="MC2000B chopper head with a Ø4\" 10/100-slot blade. It stands on the table with no post: "
            "screw it down through the two arc slots in its base, which catch two holes 3\" apart with the head "
            "centred between them and let it turn ±30° there (it snaps to such spots). Angle = the beam direction "
            "through the blade.",
            mounting=Mounting.SLOTS,
            label_prefix="CH",
            bill_of_materials=("MC2000B chopper head", "Ø4\" 10/100-slot blade", '1/4"-20 cap screws ×2'),
            builder=build_chopper,
            fields=CHOPPER_FIELDS,
            hole_anchor="Base centre",
            slots=CHOPPER_SLOTS,
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
    ]
    return {u.id: u for u in units}


UNIT_TYPES: dict[str, UnitType] = _build_registry()
CATEGORY_ORDER = (
    "Posts", "Sources", "Mirrors", "Beamsplitters", "Lenses", "Objectives", "Apertures", "Modulators", "Beam dumps",
    "Stages", "Beam expanders", "Enclosure",
)


def get_unit_type(type_id: str) -> UnitType:
    try:
        return UNIT_TYPES[type_id]
    except KeyError as exc:
        raise KeyError(f"unknown unit type {type_id!r}") from exc


def new_component_params(type_id: str) -> dict:
    return get_unit_type(type_id).default_params()


def _merged(defaults: dict, params: dict) -> dict:
    """``defaults`` overridden by ``params``, group by group (deep copies)."""
    out = copy.deepcopy(defaults)
    for key, value in params.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merged(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def with_defaults(type_id: str, params: dict | None) -> dict:
    """``params`` with ``type_id``'s defaults filled in where missing (a deep copy)."""
    return _merged(new_component_params(type_id), params or {})


# ---------------------------------------------------------- mounts on posts
def post_params(comp: Component) -> dict:
    """The parameters of ``comp``'s post (holder, clamp, KB1X1), without its mount's."""
    return {key: copy.deepcopy(comp.params[key]) for key in POST_KEYS if key in comp.params}


def mount_on_post_params(post: Component, type_id: str, mount_params: dict | None = None) -> dict:
    """Parameters for ``type_id`` standing on ``post``: its own (defaults, or
    ``mount_params``) with the post's holder, clamp and KB1X1 kept.  A mount
    put on a KB1X1 sits on it."""
    own = {key: value for key, value in (mount_params or {}).items() if key not in POST_KEYS}
    params = _merged(with_defaults(type_id, own), post_params(post))
    if has_kb1x1(post):
        set_param(params, KB1X1_SEATED.key, True)
    return params


# --------------------------------------------------------- library entries
@dataclass(frozen=True)
class LibraryEntry:
    """One item of the parts library: a unit type, possibly with preset parameters."""

    key: str
    type_id: str
    name: str
    category: str
    description: str
    params: tuple[tuple[str, Any], ...] = ()  # (dotted key, value) presets
    kb1x1_only: bool = False  # dropped on a post, it adds a KB1X1 there instead of a mount

    def initial_params(self, overrides: dict | None = None) -> dict:
        """The unit's defaults with this entry's presets, then ``overrides`` (group by group)."""
        params = new_component_params(self.type_id)
        for key, value in self.params:
            set_param(params, key, value)
        return _merged(params, overrides or {})

    @property
    def mounts_on_posts(self) -> bool:
        """Dropped on a post, it goes onto that post (a mount, or a KB1X1)."""
        return self.kb1x1_only or (stands_on_post(self.type_id) and self.type_id != BARE_POST)


def _library() -> dict[str, LibraryEntry]:
    entries = [
        LibraryEntry(
            BARE_POST, BARE_POST, "Post", "Posts",
            'Ø1/2" post in a PH2 post holder screwed into a hole. Drop mounts (or a KB1X1) onto it; '
            "right-click to clamp it on a pedestal instead.",
        ),
        LibraryEntry(
            "post-clamped", BARE_POST, "Clamped post", "Posts",
            "Post holder on a BE1 pedestal, held down anywhere by a CF125C clamping fork that finds a hole by "
            "itself. Right-click for the short CF038 clamp, or to turn the clamp.",
            params=((BASE_KIND_FIELD.key, "clamped"),),
        ),
        LibraryEntry(
            "kb1x1", BARE_POST, "KB1X1 magnetic base", "Posts",
            "Drop it on a post, then the mount on top: the mount can then be lifted off and put back "
            "repeatably (K). Dropped elsewhere, it comes on a new post.",
            params=((KB1X1_PRESENT.key, True),),
            kb1x1_only=True,
        ),
    ]
    for unit in UNIT_TYPES.values():
        if unit.id != BARE_POST:
            entries.append(LibraryEntry(unit.id, unit.id, unit.name, unit.category, unit.description))
    return {entry.key: entry for entry in entries}


def library_entry(key: str) -> LibraryEntry:
    """The library entry ``key``, or a plain one for any unit type id (an id
    from an older version, e.g. "km-mirror", comes on the holder it had)."""
    entry = LIBRARY.get(key)
    if entry is not None:
        return entry
    if key in _LEGACY_TYPES:
        type_id, holder, kb1x1 = _LEGACY_TYPES[key]
        unit = get_unit_type(type_id)
        presets = ((BASE_KIND_FIELD.key, holder), (KB1X1_PRESENT.key, kb1x1))
        return LibraryEntry(key, type_id, unit.name, unit.category, unit.description, params=presets)
    unit = get_unit_type(key)
    return LibraryEntry(unit.id, unit.id, unit.name, unit.category, unit.description)


def component_bom(comp: Component) -> tuple[str, ...]:
    """Everything a component is built from, its post and holder included."""
    unit = get_unit_type(comp.type)
    if unit.mounting is not Mounting.POST:
        return unit.bill_of_materials
    holder = ["PH2", *(("BE1", P.clamp_spec(clamp_of(comp)).part_id) if is_clamped(comp) else ())]
    return (*unit.bill_of_materials, *(("KB1X1",) if has_kb1x1(comp) else ()), *holder, 'TR-series Ø1/2" post')


# ------------------------------------------------ files from older versions
# Before layout format 2, the holder was part of the unit type (km-mirror on a
# pedestal, km-h-mirror in a hole, kb-mirror with a KB1X1, ...).  Each old id:
# (unit type now, holder, KB1X1).
_LEGACY_TYPES: dict[str, tuple[str, str, bool]] = {
    "km-mirror": ("km100", "clamped", False),
    "km-h-mirror": ("km100cp", "hole", False),
    "sq-km-mirror": ("kmsr", "clamped", False),
    "mirror": ("fmp1", "clamped", False),
    "h-mirror": ("fmp1", "hole", False),
    "kb-mirror": ("fmp1", "clamped", True),
    "kb-h-mirror": ("fmp1", "hole", True),
    "h-lens": ("lens", "hole", False),
    "h-iris": ("iris", "hole", False),
    "h-objective": ("objective", "hole", False),
    "base": (BARE_POST, "clamped", False),
    "mag-km-pellicle": ("pellicle-km100bp", "clamped", True),
    "mag-km-h-pellicle": ("pellicle-km100bp", "hole", True),
    "mag-pellicle": ("pellicle-bp107", "clamped", True),
    "mag-h-pellicle": ("pellicle-bp107", "hole", True),
    "mag-km-pellicle-2in": ("pellicle-km200bp", "clamped", True),
    "mag-km-h-pellicle-2in": ("pellicle-km200bp", "hole", True),
    "mag-pellicle-2in": ("pellicle-bp207", "clamped", True),
    "mag-h-pellicle-2in": ("pellicle-bp207", "hole", True),
}
# Unit types that kept their id: these stood on a pedestal base before version 2.
_LEGACY_PEDESTAL_IDS = frozenset({"lens", "iris", "objective", "knife-edge"})


def upgrade_component(comp: Component) -> Component:
    """Bring a component from an older file (or clipboard) up to date; a
    current one is returned unchanged."""
    legacy = _LEGACY_TYPES.get(comp.type)
    if legacy is not None:
        type_id, holder, kb1x1 = legacy
    elif stands_on_post(comp.type) and "base" not in comp.params:
        type_id, kb1x1 = comp.type, False
        holder = "clamped" if comp.type in _LEGACY_PEDESTAL_IDS else "hole"
    else:
        return comp
    params = copy.deepcopy(comp.params)
    fork = params.pop("fork", None)
    old_angle = fork.get("angle") if isinstance(fork, dict) else None
    set_param(params, BASE_KIND_FIELD.key, holder)
    if holder == "clamped" and isinstance(old_angle, (int, float)):
        # The old preference was relative to the unit; the clamp's direction is now on the table.
        set_param(params, CLAMP_ANGLE_FIELD.key, round(normalize_angle(old_angle + comp.angle), 6))
    set_param(params, KB1X1_PRESENT.key, kb1x1)
    return Component(
        id=comp.id,
        type=type_id,
        x=comp.x,
        y=comp.y,
        angle=comp.angle,
        label=comp.label,
        orientation=comp.orientation,
        params=with_defaults(type_id, params),
    )



def catalog_parts() -> list[PlacedPart]:
    """Every distinct part orientation the catalog produces: each unit in each
    orientation, with default parameters and with each on/off option flipped,
    each post-mounted unit on each kind of holder, with and without a KB1X1
    (its mount on it or lifted off), plus every clamping fork."""
    seen: dict[tuple, PlacedPart] = {}

    def add(parts) -> None:
        for part in parts:
            seen.setdefault((part.part_id, part.prims, part.roll, part.z_offset), part)

    for unit in UNIT_TYPES.values():
        variants = [unit.default_params()]
        for f in unit.fields:
            if f.kind == "bool" and not f.key.startswith("kb1x1."):
                params = unit.default_params()
                set_param(params, f.key, not f.default)
                variants.append(params)
        if unit.mounting is Mounting.POST:
            for holder, kb1x1, seated in (("clamped", False, True), ("hole", True, True), ("hole", True, False)):
                params = unit.default_params()
                set_param(params, BASE_KIND_FIELD.key, holder)
                set_param(params, KB1X1_PRESENT.key, kb1x1)
                set_param(params, KB1X1_SEATED.key, seated)
                variants.append(params)
        for orientation in [name for name, _ in unit.orientations()] or [""]:
            for params in variants:
                build = unit.build(Component("catalog", unit.id, orientation=orientation, params=params))
                add(build.parts + build.detached_parts)
    for clamp in P.CLAMPS:
        add(fork_parts(default_fork(clamp)))
    return list(seen.values())


UPGRADE_REGISTERED = register_component_upgrade(upgrade_component)
LIBRARY: dict[str, LibraryEntry] = _library()
