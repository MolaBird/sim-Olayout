"""The editable document: layout + undo stack + selection + live analysis.

Undo uses whole-layout snapshots (layouts are a few KB of JSON), which keeps
every edit trivially reversible and impossible to get out of sync.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from olayout.catalog.units import Mounting, get_unit_type, new_component_params, set_param
from olayout.core.breadboard import Breadboard
from olayout.core.layout import Component, Layout, Measurement, Probe
from olayout.core.units import normalize_angle
from olayout.engine.analysis import Analysis, Analyzer

log = logging.getLogger(__name__)

MERGE_WINDOW_S = 2.0


class SnapshotCommand(QUndoCommand):
    """Undo step holding the layout before and after an edit."""

    MERGE_ID = 4242

    def __init__(self, doc: LayoutDocument, before: dict, after: dict, text: str, merge_key: str | None):
        super().__init__(text)
        self.doc = doc
        self.before = before
        self.after = after
        self.merge_key = merge_key
        self.stamp = time.monotonic()
        self._skip_first_redo = True  # the edit is already applied when pushed

    def id(self) -> int:  # noqa: A003 - Qt API
        return self.MERGE_ID if self.merge_key else -1

    def mergeWith(self, other: QUndoCommand) -> bool:  # noqa: N802 - Qt API
        if not isinstance(other, SnapshotCommand) or other.merge_key != self.merge_key:
            return False
        if other.stamp - self.stamp > MERGE_WINDOW_S:
            return False
        self.after = other.after
        self.stamp = other.stamp
        return True

    def redo(self) -> None:
        if self._skip_first_redo:
            self._skip_first_redo = False
            return
        self.doc._restore(self.after)

    def undo(self) -> None:
        self.doc._restore(self.before)


class LayoutDocument(QObject):
    layoutReset = Signal()  # the whole layout may have changed
    componentsChanged = Signal(object)  # set of component ids whose pose/params changed
    annotationsChanged = Signal()  # measurements / probes
    analysisUpdated = Signal()
    selectionChanged = Signal()
    fileStateChanged = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.layout = Layout()
        self.path: Path | None = None
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(500)
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)
        self.analyzer = Analyzer()
        self.analysis = Analysis()
        self.selection: list[str] = []
        self.revision = 0  # bumps on every change (used by autosave)
        self._analysis_pending = False
        self._interaction_before: dict | None = None

    # ================================================================ state
    def _on_clean_changed(self, _clean: bool) -> None:
        self.fileStateChanged.emit()

    @property
    def dirty(self) -> bool:
        return not self.undo_stack.isClean()

    @property
    def display_name(self) -> str:
        return self.path.name if self.path else "Untitled"

    def snapshot(self) -> dict:
        return self.layout.to_dict()

    def _restore(self, snapshot: dict) -> None:
        self.layout = Layout.from_dict(snapshot)
        self._prune_selection()
        self._changed(None)

    def _changed(self, ids: set[str] | None) -> None:
        """Notify views; ``ids=None`` means a structural change."""
        self.revision += 1
        if ids is None:
            self.layoutReset.emit()
        elif ids:
            self.componentsChanged.emit(set(ids))
        self.request_analysis()

    # ============================================================= analysis
    def request_analysis(self) -> None:
        """Coalesce analysis requests into one pass per event-loop turn."""
        if not self._analysis_pending:
            self._analysis_pending = True
            QTimer.singleShot(0, self.analyze_now)

    def analyze_now(self) -> None:
        self._analysis_pending = False
        try:
            self.analysis = self.analyzer.run(self.layout)
        except Exception:  # never let a bad layout take the UI down
            log.exception("analysis failed")
            self.analysis = Analysis()
        self.analysisUpdated.emit()

    # ============================================================ selection
    def set_selection(self, ids: Iterable[str]) -> None:
        valid = {c.id for c in self.layout.components}
        new = list(dict.fromkeys(i for i in ids if i in valid))
        if new != self.selection:
            self.selection = new
            self.selectionChanged.emit()

    def _prune_selection(self) -> None:
        valid = {c.id for c in self.layout.components}
        pruned = [i for i in self.selection if i in valid]
        if pruned != self.selection:
            self.selection = pruned
            self.selectionChanged.emit()

    def selected_components(self) -> list[Component]:
        return [c for i in self.selection if (c := self.layout.component(i)) is not None]

    # ================================================================ edits
    def apply(
        self,
        text: str,
        mutator: Callable[[Layout], None],
        changed: set[str] | None = None,
        merge_key: str | None = None,
    ) -> bool:
        """Run ``mutator`` on the layout as one undoable step."""
        before = self.snapshot()
        mutator(self.layout)
        after = self.snapshot()
        if after == before:
            return False
        self.undo_stack.push(SnapshotCommand(self, before, after, text, merge_key))
        self._prune_selection()
        self._changed(changed)
        return True

    # Interactive edits (dragging): mutate in place, then commit once.
    def begin_interaction(self) -> None:
        self._interaction_before = self.snapshot()

    @property
    def interacting(self) -> bool:
        return self._interaction_before is not None

    def interaction_changed(self, ids: set[str]) -> None:
        self._changed(ids)

    def end_interaction(self, text: str) -> None:
        before, self._interaction_before = self._interaction_before, None
        if before is None:
            return
        after = self.snapshot()
        if after != before:
            self.undo_stack.push(SnapshotCommand(self, before, after, text, None))
            self.revision += 1

    def cancel_interaction(self) -> None:
        before, self._interaction_before = self._interaction_before, None
        if before is not None:
            self._restore(before)

    # ------------------------------------------------------ component edits
    def add_component(
        self,
        type_id: str,
        x: float,
        y: float,
        angle: float = 0.0,
        params: dict | None = None,
        orientation: str = "",
        select: bool = True,
    ) -> str:
        unit = get_unit_type(type_id)
        new_id = self.layout.new_id("c")

        def mutate(layout: Layout) -> None:
            comp = Component(
                id=new_id,
                type=type_id,
                x=x,
                y=y,
                angle=normalize_angle(angle),
                label=layout.new_label(unit.label_prefix),
                orientation=orientation,
                params=params if params is not None else new_component_params(type_id),
            )
            layout.components.append(comp)

        self.apply(f"Add {unit.name}", mutate)
        if select:
            self.set_selection([new_id])
        return new_id

    def delete_components(self, ids: Iterable[str]) -> None:
        doomed = set(ids)
        if not doomed:
            return

        def mutate(layout: Layout) -> None:
            layout.components = [c for c in layout.components if c.id not in doomed]

        self.apply(f"Delete {len(doomed)} item(s)", mutate)

    def insert_components(self, comps: list[Component], text: str) -> list[str]:
        """Insert copies (fresh ids, fresh labels) and select them."""
        new_ids: list[str] = []

        def mutate(layout: Layout) -> None:
            for comp in comps:
                unit = get_unit_type(comp.type)
                clone = Component.from_dict(comp.to_dict())
                clone.id = layout.new_id("c")
                clone.label = layout.new_label(unit.label_prefix)
                layout.components.append(clone)
                new_ids.append(clone.id)

        self.apply(text, mutate)
        self.set_selection(new_ids)
        return new_ids

    def set_component_fields(self, comp_id: str, merge: bool = True, **fields) -> None:
        """Set x / y / angle / label / orientation on one component."""
        if "angle" in fields:
            fields["angle"] = normalize_angle(float(fields["angle"]))

        def mutate(layout: Layout) -> None:
            comp = layout.component(comp_id)
            if comp is not None:
                for key, value in fields.items():
                    setattr(comp, key, value)

        key = f"{comp_id}:{','.join(sorted(fields))}" if merge else None
        self.apply(f"Edit {', '.join(sorted(fields))}", mutate, changed={comp_id}, merge_key=key)

    def set_component_param(self, comp_id: str, key: str, value) -> None:
        def mutate(layout: Layout) -> None:
            comp = layout.component(comp_id)
            if comp is not None:
                set_param(comp.params, key, value)

        self.apply(f"Edit {key}", mutate, changed={comp_id}, merge_key=f"{comp_id}:{key}")

    def transform_components(self, poses: dict[str, tuple[float, float, float]], text: str) -> None:
        """Set (x, y, angle) for several components as one step."""

        def mutate(layout: Layout) -> None:
            for cid, (x, y, angle) in poses.items():
                comp = layout.component(cid)
                if comp is not None:
                    comp.x, comp.y, comp.angle = x, y, normalize_angle(angle)

        self.apply(text, mutate, changed=set(poses))

    def cycle_orientation(self, ids: Iterable[str]) -> None:
        changes: dict[str, str] = {}
        for cid in ids:
            comp = self.layout.component(cid)
            if comp is None:
                continue
            options = [name for name, _ in get_unit_type(comp.type).orientations()]
            if len(options) < 2:
                continue
            current = comp.orientation or options[0]
            index = options.index(current) if current in options else 0
            changes[cid] = options[(index + 1) % len(options)]
        if not changes:
            return

        def mutate(layout: Layout) -> None:
            for cid, value in changes.items():
                comp = layout.component(cid)
                if comp is not None:
                    comp.orientation = value

        self.apply("Change mounting orientation", mutate, changed=set(changes))

    # ----------------------------------------------------- annotation edits
    def add_measurement(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        def mutate(layout: Layout) -> None:
            layout.measurements.append(Measurement(layout.new_id("m"), a, b))

        self.apply("Add measurement", mutate, changed=set())
        self.annotationsChanged.emit()

    def add_probe(self, x: float, y: float) -> None:
        def mutate(layout: Layout) -> None:
            pid = layout.new_id("p")
            layout.probes.append(Probe(pid, x, y, label=f"P{len(layout.probes) + 1}"))

        self.apply("Add beam probe", mutate, changed=set())
        self.annotationsChanged.emit()

    def update_annotation(self, ann_id: str, **fields) -> None:
        def mutate(layout: Layout) -> None:
            for item in [*layout.measurements, *layout.probes]:
                if item.id == ann_id:
                    for key, value in fields.items():
                        setattr(item, key, value)

        self.apply("Edit annotation", mutate, changed=set())
        self.annotationsChanged.emit()

    def delete_annotations(self, ids: Iterable[str]) -> None:
        doomed = set(ids)

        def mutate(layout: Layout) -> None:
            layout.measurements = [m for m in layout.measurements if m.id not in doomed]
            layout.probes = [p for p in layout.probes if p.id not in doomed]

        if self.apply("Delete annotation", mutate, changed=set()):
            self.annotationsChanged.emit()

    def clear_annotations(self) -> None:
        def mutate(layout: Layout) -> None:
            layout.measurements.clear()
            layout.probes.clear()

        if self.apply("Clear measurements and probes", mutate, changed=set()):
            self.annotationsChanged.emit()

    def set_breadboard(self, board: Breadboard) -> None:
        def mutate(layout: Layout) -> None:
            layout.breadboard = board

        self.apply("Change breadboard", mutate)

    def set_notes(self, text: str) -> None:
        def mutate(layout: Layout) -> None:
            layout.notes = text

        self.apply("Edit notes", mutate, changed=set(), merge_key="notes")

    # ================================================================ files
    def new_layout(self, board: Breadboard | None = None) -> None:
        self.layout = Layout(breadboard=board or Breadboard())
        self.path = None
        self.selection = []
        self.undo_stack.clear()
        self.selectionChanged.emit()
        self._changed(None)
        self.fileStateChanged.emit()

    def open(self, path: str | Path) -> None:
        """Load a layout; raises ``LayoutFormatError`` / ``OSError`` on failure."""
        layout = Layout.load(path)
        self.layout = layout
        self.path = Path(path)
        self.selection = []
        self.undo_stack.clear()
        self.selectionChanged.emit()
        self._changed(None)
        self.fileStateChanged.emit()

    def load_layout(self, layout: Layout, path: Path | None = None, dirty: bool = False) -> None:
        """Adopt an already-parsed layout (e.g. crash recovery)."""
        if dirty:
            before = self.snapshot()
            self.layout = layout
            self.undo_stack.push(SnapshotCommand(self, before, layout.to_dict(), "Recover layout", None))
        else:
            self.layout = layout
            self.undo_stack.clear()
        self.path = path
        self.selection = []
        self.selectionChanged.emit()
        self._changed(None)
        self.fileStateChanged.emit()

    def save(self, path: str | Path | None = None) -> None:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("no file name given")
        self.layout.save(target)
        self.path = target
        self.undo_stack.setClean()
        self.fileStateChanged.emit()

    # ============================================================== queries
    def unit_mounting(self, comp: Component) -> Mounting:
        return get_unit_type(comp.type).mounting
