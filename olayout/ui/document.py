"""The editable document: layout + undo stack + selection + live analysis.

Undo uses whole-layout snapshots (layouts are a few KB of JSON), which keeps
every edit trivially reversible and impossible to get out of sync.
"""

from __future__ import annotations

import copy
import logging
import re
import time
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from olayout.catalog import parts as P
from olayout.catalog.units import (
    BARE_POST,
    BASE_KIND_FIELD,
    CLAMP_ANGLE_FIELD,
    CLAMP_FIELD,
    KB1X1_PRESENT,
    KB1X1_SEATED,
    LIBRARY,
    UNIT_TYPES,
    Mounting,
    get_unit_type,
    has_kb1x1,
    has_mount,
    is_clamped,
    lifted_off,
    mount_on_post_params,
    mounting_of,
    post_params,
    set_param,
    stands_on_post,
    upgrade_component,
    with_defaults,
)
from olayout.core.breadboard import Breadboard
from olayout.core.layout import Component, IssueKey, Layout, Measurement, Probe
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
        proto = _current(Component(id="", type=type_id, orientation=orientation, params=params or {}))
        unit = get_unit_type(proto.type)
        new_id = self.layout.new_id("c")

        def mutate(layout: Layout) -> None:
            comp = Component(
                id=new_id,
                type=proto.type,
                x=x,
                y=y,
                angle=normalize_angle(angle),
                label=layout.new_label(unit.label_prefix),
                orientation=proto.orientation,
                params=proto.params,
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
            layout.remove_components(doomed)

        self.apply(f"Delete {len(doomed)} item(s)", mutate)

    def insert_components(self, comps: list[Component], text: str) -> list[str]:
        """Insert copies (fresh ids, fresh labels) and select them."""
        new_ids: list[str] = []

        def mutate(layout: Layout) -> None:
            for comp in comps:
                clone = upgrade_component(Component.from_dict(comp.to_dict()))  # e.g. pasted from an older version
                unit = get_unit_type(clone.type)
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

    def transform_components(
        self, poses: dict[str, tuple[float, float, float]], text: str, merge_key: str | None = None
    ) -> None:
        """Set (x, y, angle) for several components as one step (merged with the
        previous one of the same ``merge_key`` if it was moments ago)."""

        def mutate(layout: Layout) -> None:
            for cid, (x, y, angle) in poses.items():
                comp = layout.component(cid)
                if comp is not None:
                    comp.x, comp.y, comp.angle = x, y, normalize_angle(angle)

        self.apply(text, mutate, changed=set(poses), merge_key=merge_key)

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

    def toggle_lifted_off(self, ids: Iterable[str]) -> None:
        """Lift KB1X1-mounted optics off their bases, or put them back (one undo step).

        With any of them on its base, all come off; otherwise all go back.
        """
        comps = [c for cid in ids if (c := self.layout.component(cid)) is not None and has_kb1x1(c) and has_mount(c)]
        if not comps:
            return
        take_off = any(not lifted_off(c) for c in comps)
        ids = {c.id for c in comps}

        def mutate(layout: Layout) -> None:
            for comp in layout.components:
                if comp.id in ids:
                    set_param(comp.params, KB1X1_SEATED.key, not take_off)

        self.apply("Lift off KB1X1" if take_off else "Put back on KB1X1", mutate, changed=ids)

    # ----------------------------------------------------------- post edits
    def _posts(self, ids: Iterable[str]) -> list[Component]:
        return [c for cid in ids if (c := self.layout.component(cid)) is not None and stands_on_post(c.type)]

    def _edit_posts(self, ids: Iterable[str], text: str, edit: Callable[[Layout, Component], None]) -> bool:
        """Apply ``edit`` to each of ``ids`` that stands on a post, as one undo step."""
        targets = {c.id for c in self._posts(ids)}
        if not targets:
            return False

        def mutate(layout: Layout) -> None:
            for comp in layout.components:
                if comp.id in targets:
                    edit(layout, comp)

        return self.apply(text, mutate, changed=targets)

    def mount_on_post(
        self,
        post_id: str,
        type_id: str,
        params: dict | None = None,
        orientation: str = "",
        angle: float | None = None,
    ) -> bool:
        """Put a mount (any post-mounted unit) on a bare post, which keeps its
        holder, clamp and KB1X1.  ``angle`` turns it (None: as the post is)."""
        post = self.layout.component(post_id)
        if post is None or post.type != BARE_POST or not stands_on_post(type_id) or type_id == BARE_POST:
            return False
        unit = get_unit_type(type_id)

        def mutate(layout: Layout) -> None:
            comp = layout.component(post_id)
            comp.params = mount_on_post_params(comp, type_id, params)
            comp.orientation = orientation
            if angle is not None:
                comp.angle = normalize_angle(angle)
            _retype(layout, comp, type_id)

        done = self.apply(f"Put {unit.name} on {post.label or 'post'}", mutate, changed={post_id})
        if done:
            self.set_selection([post_id])
        return done

    def take_off_post(self, ids: Iterable[str]) -> bool:
        """Take the mounts off these posts, leaving the posts (holder, clamp, KB1X1)."""

        def edit(layout: Layout, comp: Component) -> None:
            comp.params = with_defaults(BARE_POST, post_params(comp))
            comp.orientation = ""
            _retype(layout, comp, BARE_POST)

        return self._edit_posts([c.id for c in self._posts(ids) if has_mount(c)], "Take mount off post", edit)

    def move_mount(
        self,
        source_id: str,
        post_id: str | None = None,
        at: tuple[float, float, float] | None = None,
    ) -> str | None:
        """Move the mount on ``source_id`` onto the bare post ``post_id``, or
        (``post_id`` None) onto a new post at ``at`` = (x, y, angle) in the same
        kind of holder.  The source post stays, bare.  Returns the mount's new id."""
        source = self.layout.component(source_id)
        if source is None or not has_mount(source):
            return None
        target = self.layout.component(post_id) if post_id else None
        if post_id is not None and (target is None or target.type != BARE_POST):
            return None
        if target is None and at is None:
            return None
        new_id = post_id or self.layout.new_id("c")
        type_id, label, orientation = source.type, source.label, source.orientation
        mount_params = copy.deepcopy(source.params)

        def mutate(layout: Layout) -> None:
            src = layout.component(source_id)
            src.params = with_defaults(BARE_POST, post_params(src))
            src.orientation = ""
            _retype(layout, src, BARE_POST)  # a fresh post label, freeing the mount's
            if post_id is not None:
                comp = layout.component(post_id)
                if at is not None:
                    comp.angle = normalize_angle(at[2])
            else:
                comp = Component(new_id, BARE_POST, at[0], at[1], normalize_angle(at[2]))
                comp.params = with_defaults(BARE_POST, {"base": copy.deepcopy(mount_params.get("base", {}))})
                set_param(comp.params, CLAMP_ANGLE_FIELD.key, None)  # a new spot: it finds its own hole
                if not is_clamped(comp):
                    comp.x, comp.y = layout.breadboard.nearest_hole(comp.x, comp.y)
                layout.components.append(comp)
            comp.params = mount_on_post_params(comp, type_id, mount_params)
            comp.type, comp.orientation, comp.label = type_id, orientation, label

        if not self.apply(f"Move {label or 'mount'}", mutate):
            return None
        self.set_selection([new_id])
        return new_id

    def set_base_kind(self, ids: Iterable[str], kind: str) -> bool:
        """Screw post holders into holes (each moves to its nearest hole) or clamp them on pedestals."""
        board = self.layout.breadboard

        def edit(layout: Layout, comp: Component) -> None:
            set_param(comp.params, BASE_KIND_FIELD.key, kind)
            if kind == "hole":
                comp.x, comp.y = board.nearest_hole(comp.x, comp.y)

        what = "Clamp on pedestal" if kind == "clamped" else "Screw into hole"
        return self._edit_posts(ids, what, edit)

    def set_clamp(self, ids: Iterable[str], clamp: str) -> bool:
        clamp = P.clamp_spec(clamp).part_id

        def edit(layout: Layout, comp: Component) -> None:
            set_param(comp.params, CLAMP_FIELD.key, clamp)

        return self._edit_posts([c.id for c in self._posts(ids) if is_clamped(c)], f"Use {clamp} clamp", edit)

    def set_clamp_angle(self, ids: Iterable[str], angle: float | None) -> bool:
        """Point the clamps this way on the table (None: each finds its own hole)."""
        value = None if angle is None else round(normalize_angle(angle), 2)

        def edit(layout: Layout, comp: Component) -> None:
            set_param(comp.params, CLAMP_ANGLE_FIELD.key, value)

        targets = [c.id for c in self._posts(ids) if is_clamped(c)]
        return self._edit_posts(targets, "Automatic clamp" if value is None else "Turn clamp", edit)

    def set_kb1x1(self, ids: Iterable[str], present: bool) -> bool:
        """Put KB1X1 magnetic bases on these posts (under their mounts), or take them away."""

        def edit(layout: Layout, comp: Component) -> None:
            set_param(comp.params, KB1X1_PRESENT.key, present)
            set_param(comp.params, KB1X1_SEATED.key, True)

        targets = [c.id for c in self._posts(ids) if has_kb1x1(c) != present]
        return self._edit_posts(targets, "Add KB1X1" if present else "Remove KB1X1", edit)

    # ---------------------------------------------------------- board edits
    def drill_hole(self, x: float, y: float) -> bool:
        """Drill a tapped hole at the grid-cell centre (x, y); False if it can't be."""
        board = self.layout.breadboard
        if board.drill_problem(x, y) is not None:
            return False
        return self._set_board(board.with_drilled(x, y), "Drill hole")

    def fill_hole(self, x: float, y: float) -> bool:
        """Fill in the drilled hole at (x, y)."""
        return self._set_board(self.layout.breadboard.without_drilled(x, y), "Fill drilled hole")

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

    def set_ignored(self, keys: Iterable[IssueKey], ignored: bool = True) -> None:
        """Accept problems (they stop counting as illegal) or restore them; undoable."""
        keys = set(keys)
        if not keys:
            return

        def mutate(layout: Layout) -> None:
            layout.ignored = layout.ignored | keys if ignored else layout.ignored - keys

        verb = "Ignore" if ignored else "Restore"
        self.apply(f"{verb} {len(keys)} problem(s)", mutate, changed=set())

    def restore_all_ignored(self) -> None:
        def mutate(layout: Layout) -> None:
            layout.ignored = set()

        self.apply("Restore ignored problems", mutate, changed=set())

    def set_breadboard(self, board: Breadboard) -> None:
        """Change the breadboard; drilled holes stay where they are still at cell centres."""
        if not board.drilled:
            for hole in self.layout.breadboard.drilled:
                board = board.with_drilled(*hole)
        self._set_board(board, "Change breadboard")

    def _set_board(self, board: Breadboard, text: str) -> bool:
        def mutate(layout: Layout) -> None:
            layout.breadboard = board

        return self.apply(text, mutate)

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
        return mounting_of(comp)


def _current(comp: Component) -> Component:
    """A component as this version builds it: a library preset (e.g. a clamped
    post) becomes its unit type, an old type id is upgraded, and missing
    parameters take their defaults."""
    if comp.type in LIBRARY and comp.type not in UNIT_TYPES:
        entry = LIBRARY[comp.type]
        comp.type, comp.params = entry.type_id, entry.initial_params(comp.params)
    elif comp.type not in UNIT_TYPES:
        comp = upgrade_component(comp)
    if comp.type in UNIT_TYPES:
        comp.params = with_defaults(comp.type, comp.params)
    return comp


def _retype(layout: Layout, comp: Component, type_id: str) -> None:
    """Change what a component is; an automatic label (e.g. PH3) follows (e.g. to M7)."""
    old, new = UNIT_TYPES[comp.type].label_prefix, UNIT_TYPES[type_id].label_prefix
    comp.type = type_id
    if old != new and (not comp.label or re.fullmatch(rf"{re.escape(old)}\d+", comp.label)):
        comp.label = layout.new_label(new)
