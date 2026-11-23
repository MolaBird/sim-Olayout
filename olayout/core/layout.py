"""The layout document: breadboard, placed components, measurements, probes.

This module is pure data + JSON (de)serialization; it knows nothing about
part geometry.  Files are plain, indented JSON so they are easy to read,
diff and hand-edit.  Units are spelled out in the keys where they are not
inches (e.g. ``wavelength_nm``).
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from olayout.core.breadboard import Breadboard
from olayout.core.geometry import Pose
from olayout.core.units import normalize_angle

FORMAT_NAME = "olayout"
FORMAT_VERSION = 1
FILE_SUFFIX = ".olayout.json"


class LayoutFormatError(ValueError):
    """Raised when a layout file cannot be understood."""


def _float(data: dict, key: str, default: float) -> float:
    value = data.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise LayoutFormatError(f"{key!r} must be a number, got {value!r}") from exc


def _point(value: Any, what: str) -> tuple[float, float]:
    try:
        x, y = value
        return float(x), float(y)
    except (TypeError, ValueError) as exc:
        raise LayoutFormatError(f"{what} must be [x, y], got {value!r}") from exc


@dataclass
class Component:
    """One placed unit (e.g. a kinematic mirror on a pedestal base).

    ``x, y`` is the post axis on the table; ``angle`` is the direction the
    optic's front face points (for sources: the beam direction).
    """

    id: str
    type: str
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0
    label: str = ""
    orientation: str = ""  # mounting tap name; "" means the unit's default
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def pose(self) -> Pose:
        return Pose(self.x, self.y, self.angle)

    def to_dict(self) -> dict:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "x": round(self.x, 6),
            "y": round(self.y, 6),
            "angle": round(normalize_angle(self.angle), 6),
        }
        if self.label:
            data["label"] = self.label
        if self.orientation:
            data["orientation"] = self.orientation
        if self.params:
            data["params"] = copy.deepcopy(self.params)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Component:
        if not isinstance(data, dict):
            raise LayoutFormatError(f"component entries must be objects, got {data!r}")
        if "type" not in data:
            raise LayoutFormatError(f"component {data.get('id', '?')!r} has no 'type'")
        params = data.get("params", {})
        if not isinstance(params, dict):
            raise LayoutFormatError(f"component {data.get('id', '?')!r}: 'params' must be an object")
        return cls(
            id=str(data.get("id", "")),
            type=str(data["type"]),
            x=_float(data, "x", 0.0),
            y=_float(data, "y", 0.0),
            angle=_float(data, "angle", 0.0),
            label=str(data.get("label", "")),
            orientation=str(data.get("orientation", "")),
            params=copy.deepcopy(params),
        )


@dataclass
class Measurement:
    """A persistent dimension line between two table points."""

    id: str
    a: tuple[float, float]
    b: tuple[float, float]

    def to_dict(self) -> dict:
        return {"id": self.id, "a": [round(v, 6) for v in self.a], "b": [round(v, 6) for v in self.b]}

    @classmethod
    def from_dict(cls, data: dict) -> Measurement:
        return cls(id=str(data.get("id", "")), a=_point(data.get("a"), "measurement 'a'"), b=_point(data.get("b"), "measurement 'b'"))


@dataclass
class Probe:
    """A pinned beam probe; it reports beam parameters at the nearest beam point."""

    id: str
    x: float
    y: float
    label: str = ""

    def to_dict(self) -> dict:
        data = {"id": self.id, "x": round(self.x, 6), "y": round(self.y, 6)}
        if self.label:
            data["label"] = self.label
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Probe:
        return cls(
            id=str(data.get("id", "")),
            x=_float(data, "x", 0.0),
            y=_float(data, "y", 0.0),
            label=str(data.get("label", "")),
        )


@dataclass(frozen=True, order=True)
class IssueKey:
    """Identity of a placement problem or beam warning, stable across analyses.

    The layout keeps the keys of the problems the user chose to ignore
    (accept), so the choice survives edits, undo and saving.
    """

    kind: str  # e.g. "overlap", "off-hole", "clipped"
    components: tuple[str, ...]  # ids of the components involved, sorted
    layer: str = ""  # overlaps only: "base" or "optic"

    @classmethod
    def of(cls, kind: str, components, layer: str = "") -> IssueKey:
        return cls(kind, tuple(sorted(components)), layer)

    def involves(self, component_ids) -> bool:
        return not set(self.components).isdisjoint(component_ids)

    def to_dict(self) -> dict:
        data: dict[str, Any] = {"kind": self.kind, "components": list(self.components)}
        if self.layer:
            data["layer"] = self.layer
        return data

    @classmethod
    def from_dict(cls, data: Any) -> IssueKey:
        valid = isinstance(data, dict) and isinstance(data.get("kind"), str) and isinstance(data.get("components"), list)
        if not valid:
            raise LayoutFormatError(f"ignored entries need 'kind' and 'components', got {data!r}")
        return cls.of(data["kind"], (str(c) for c in data["components"]), str(data.get("layer", "")))


@dataclass
class Layout:
    breadboard: Breadboard = field(default_factory=Breadboard)
    components: list[Component] = field(default_factory=list)
    measurements: list[Measurement] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)
    notes: str = ""
    ignored: set[IssueKey] = field(default_factory=set)  # problems the user accepted

    # -------------------------------------------------------------- lookup
    def component(self, component_id: str) -> Component | None:
        for comp in self.components:
            if comp.id == component_id:
                return comp
        return None

    def remove_components(self, ids) -> None:
        """Delete components and forget the ignored problems that involved them
        (a later component may get the same id)."""
        doomed = set(ids)
        self.components = [c for c in self.components if c.id not in doomed]
        self.ignored = {key for key in self.ignored if not key.involves(doomed)}

    def all_ids(self) -> set[str]:
        return {c.id for c in self.components} | {m.id for m in self.measurements} | {p.id for p in self.probes}

    def new_id(self, prefix: str = "c") -> str:
        """Unique id like ``c7`` (never reuses ids present in the document)."""
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        used = [int(m.group(1)) for i in self.all_ids() if (m := pattern.match(i))]
        return f"{prefix}{max(used, default=0) + 1}"

    def new_label(self, prefix: str) -> str:
        """Next free label such as ``M3`` for the given prefix."""
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        used = [int(m.group(1)) for c in self.components if (m := pattern.match(c.label))]
        return f"{prefix}{max(used, default=0) + 1}"

    # ------------------------------------------------------- serialization
    def to_dict(self) -> dict:
        data: dict[str, Any] = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "units": "inch",
            "breadboard": self.breadboard.to_dict(),
            "components": [c.to_dict() for c in self.components],
            "measurements": [m.to_dict() for m in self.measurements],
            "probes": [p.to_dict() for p in self.probes],
        }
        if self.notes:
            data["notes"] = self.notes
        if self.ignored:
            data["ignored"] = [key.to_dict() for key in sorted(self.ignored)]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Layout:
        if not isinstance(data, dict):
            raise LayoutFormatError("a layout file must contain a JSON object")
        if data.get("format", FORMAT_NAME) != FORMAT_NAME:
            raise LayoutFormatError(f"not an olayout file (format={data.get('format')!r})")
        version = data.get("version", FORMAT_VERSION)
        if not isinstance(version, int) or version > FORMAT_VERSION:
            raise LayoutFormatError(f"unsupported layout version {version!r} (this build reads <= {FORMAT_VERSION})")
        if data.get("units", "inch") != "inch":
            raise LayoutFormatError("only inch-based layouts are supported")

        layout = cls(
            breadboard=Breadboard.from_dict(data.get("breadboard", {})),
            components=[Component.from_dict(c) for c in data.get("components", [])],
            measurements=[Measurement.from_dict(m) for m in data.get("measurements", [])],
            probes=[Probe.from_dict(p) for p in data.get("probes", [])],
            notes=str(data.get("notes", "")),
        )
        ignored = data.get("ignored", [])
        if not isinstance(ignored, list):
            raise LayoutFormatError(f"'ignored' must be a list, got {ignored!r}")
        layout._repair_ids()
        present = {c.id for c in layout.components}
        layout.ignored = {key for key in map(IssueKey.from_dict, ignored) if set(key.components) <= present}
        return layout

    def _repair_ids(self) -> None:
        """Give missing or duplicate ids fresh values (hand-edited files)."""
        seen: set[str] = set()
        for items, prefix in ((self.components, "c"), (self.measurements, "m"), (self.probes, "p")):
            for item in items:
                if not item.id or item.id in seen:
                    item.id = ""
                    item.id = self.new_id(prefix)
                seen.add(item.id)

    def copy(self) -> Layout:
        return Layout.from_dict(self.to_dict())

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Layout:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LayoutFormatError(f"invalid JSON: {exc}") from exc
        return cls.from_dict(data)

    def save(self, path: str | Path) -> None:
        """Write atomically: a crash mid-save never corrupts the old file."""
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(self.to_json() + "\n", encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> Layout:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))
