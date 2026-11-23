"""Inspector: edit the selected component, or the layout when nothing is selected.

Parameter editors are generated from each unit type's :class:`ParamField`
list, so new unit types get an inspector for free.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from olayout.catalog.units import DEFAULT_THICKNESS_MM, Mounting, ParamField, get_param, get_unit_type
from olayout.core.breadboard import PRESETS, Breadboard
from olayout.core.layout import Component
from olayout.core.units import format_beam_size, normalize_angle
from olayout.optics.elements import OpticKind
from olayout.ui.document import LayoutDocument


def _spin(decimals: int, step: float, lo: float, hi: float, suffix: str = "") -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setRange(lo, hi)
    spin.setKeyboardTracking(False)  # commit on Enter / focus-out / arrows, not per keystroke
    spin.setAccelerated(True)
    if suffix:
        spin.setSuffix(f" {suffix}")
    return spin


def _separator() -> QFrame:
    line = QFrame()
    line.setObjectName("Separator")
    line.setFrameShape(QFrame.Shape.HLine)
    return line


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Heading")
    return label


def _dim(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Dim")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class InspectorPanel(QScrollArea):
    def __init__(self, doc: LayoutDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._container = QWidget()
        self._container.setObjectName("Panel")
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(12, 10, 12, 12)
        self._vbox.setSpacing(8)
        self.setWidget(self._container)

        self._comp_id: str | None = None
        self._pose_spins: dict[str, QDoubleSpinBox] = {}
        self._param_widgets: dict[str, tuple[ParamField, QWidget, QWidget]] = {}
        self._form: QFormLayout | None = None
        self._label_edit: QLineEdit | None = None
        self._orientation: QComboBox | None = None
        self._info: QLabel | None = None
        self._checks: QLabel | None = None
        self._layout_stats: QLabel | None = None
        self._board_widgets: tuple[QComboBox, QDoubleSpinBox, QDoubleSpinBox, QDoubleSpinBox] | None = None
        self._notes_edit: QPlainTextEdit | None = None
        self._showing = ""

        doc.selectionChanged.connect(self.rebuild)
        doc.layoutReset.connect(self.rebuild)
        doc.componentsChanged.connect(self._on_components_changed)
        doc.analysisUpdated.connect(self.refresh_info)
        self.rebuild()

    # ============================================================ building
    @staticmethod
    def _discard(widget: QWidget) -> None:
        # Hide first: deleteLater only runs once control returns to the event loop.
        widget.hide()
        widget.deleteLater()

    def _clear(self) -> None:
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self._discard(widget)
            elif item.layout() is not None:
                self._delete_layout(item.layout())
        self._pose_spins.clear()
        self._param_widgets.clear()
        self._form = None
        self._label_edit = None
        self._orientation = None
        self._info = None
        self._checks = None
        self._layout_stats = None
        self._board_widgets = None
        self._notes_edit = None

    def _delete_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                self._discard(item.widget())
            elif item.layout() is not None:
                self._delete_layout(item.layout())
        layout.deleteLater()

    def rebuild(self) -> None:
        selection = self.doc.selected_components()
        if not selection:
            key = "none"
        elif len(selection) == 1:
            key = f"one:{selection[0].id}:{selection[0].type}"
        else:
            key = "multi:" + ",".join(c.id for c in selection)
        if key == self._showing:  # same subject: update in place, keep focus
            if key == "none":
                self._refresh_board_values()
            else:
                self.refresh_values()
            self.refresh_info()
            return
        self._showing = key
        self._clear()
        if not selection:
            self._comp_id = None
            self._build_layout_section()
        elif len(selection) == 1:
            self._comp_id = selection[0].id
            self._build_component(selection[0])
        else:
            self._comp_id = None
            self._build_multi(selection)
        self._vbox.addStretch(1)
        self.refresh_info()

    def _build_component(self, comp: Component) -> None:
        unit = get_unit_type(comp.type)
        self._vbox.addWidget(_heading(unit.name))
        self._vbox.addWidget(_dim(unit.description))
        self._vbox.addWidget(_separator())

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        self._form = form

        self._label_edit = QLineEdit(comp.label)
        self._label_edit.editingFinished.connect(self._commit_label)
        form.addRow("Label", self._label_edit)

        hole_mounted = unit.mounting is Mounting.HOLE
        step = self.doc.layout.breadboard.pitch if hole_mounted else 0.125
        for key, label, suffix, decimals, step_value, lo, hi in (
            ("x", "X", "in", 3, step, -200.0, 400.0),
            ("y", "Y", "in", 3, step, -200.0, 400.0),
            ("angle", "Angle", "°", 2, 1.0, -720.0, 720.0),
        ):
            spin = _spin(decimals, step_value, lo, hi, suffix)
            if key == "angle":
                spin.setWrapping(True)
                spin.setRange(0.0, 360.0)
            spin.valueChanged.connect(lambda value, k=key: self._commit_pose(k, value))
            self._pose_spins[key] = spin
            form.addRow(label, spin)

        options = unit.orientations()
        if len(options) > 1:
            combo = QComboBox()
            for name, text in options:
                combo.addItem(text, name)
            combo.currentIndexChanged.connect(self._commit_orientation)
            self._orientation = combo
            form.addRow("Mounting", combo)

        for field in unit.fields:
            self._add_param_row(form, field)
        self._vbox.addLayout(form)

        self._vbox.addWidget(_separator())
        self._info = _dim("")
        self._vbox.addWidget(self._info)
        self._checks = QLabel("")
        self._checks.setObjectName("Error")
        self._checks.setWordWrap(True)
        self._vbox.addWidget(self._checks)
        if unit.bill_of_materials:
            self._vbox.addWidget(_dim("Parts: " + ", ".join(unit.bill_of_materials)))
        self.refresh_values()

    def _add_param_row(self, form: QFormLayout, field: ParamField) -> None:
        if field.kind == "bool":
            editor: QWidget = QCheckBox()
            editor.toggled.connect(lambda v, f=field: self._commit_param(f, bool(v)))
            row_widget = editor
        elif field.kind == "choice":
            editor = QComboBox()
            for value, text in field.choices:
                editor.addItem(text, value)
            editor.currentIndexChanged.connect(
                lambda _i, f=field, e=editor: self._commit_param(f, e.currentData())
            )
            row_widget = editor
        elif field.kind == "int":
            editor = QSpinBox()
            editor.setRange(int(field.minimum), int(field.maximum))
            editor.setKeyboardTracking(False)
            editor.valueChanged.connect(lambda v, f=field: self._commit_param(f, int(v)))
            row_widget = editor
        else:
            editor = _spin(field.decimals, field.step, field.minimum, field.maximum, field.unit)
            if field.default is None:  # optional value: "Auto" checkbox + spin
                auto = QCheckBox("Auto")
                row_widget = QWidget()
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(auto)
                row.addWidget(editor, 1)
                auto.toggled.connect(lambda on, f=field, e=editor: self._commit_optional(f, on, e))
                editor.valueChanged.connect(lambda v, f=field, a=auto: None if a.isChecked() else self._commit_param(f, float(v)))
                editor.setProperty("auto_box", auto)
            else:
                editor.valueChanged.connect(lambda v, f=field: self._commit_param(f, float(v)))
                row_widget = editor
        if field.tooltip:
            row_widget.setToolTip(field.tooltip)
        label = QLabel(field.label)
        form.addRow(label, row_widget)
        self._param_widgets[field.key] = (field, editor, label)

    def _build_multi(self, comps: list[Component]) -> None:
        self._vbox.addWidget(_heading(f"{len(comps)} components"))
        self._vbox.addWidget(_dim(", ".join(c.label or c.id for c in comps)))
        self._vbox.addWidget(_separator())
        self._vbox.addWidget(
            _dim("Drag to move them together.  R / Shift+R rotates each by 90°, Q / E by 15°.  Delete removes them.")
        )
        button = QPushButton("Delete selected")
        button.clicked.connect(lambda: self.doc.delete_components(self.doc.selection))
        self._vbox.addWidget(button)

    def _build_layout_section(self) -> None:
        board = self.doc.layout.breadboard
        self._vbox.addWidget(_heading("Layout"))
        self._vbox.addWidget(_dim("Nothing selected.  Pick a part from the library and drop it on the board."))
        self._vbox.addWidget(_separator())
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        preset = QComboBox()
        for name, p in PRESETS.items():
            preset.addItem(p.label, name)
        preset.addItem("Custom", "")
        index = preset.findData(board.name)
        preset.setCurrentIndex(index if index >= 0 else preset.count() - 1)
        preset.currentIndexChanged.connect(lambda _i: self._commit_preset(preset.currentData()))
        form.addRow("Breadboard", preset)
        width = _spin(2, 1.0, 2.0, 240.0, "in")
        width.setValue(board.width)
        height = _spin(2, 1.0, 2.0, 240.0, "in")
        height.setValue(board.height)
        margin = _spin(3, 0.125, 0.0, 6.0, "in")
        margin.setValue(board.margin)
        for spin, text in ((width, "Width"), (height, "Height"), (margin, "Hole margin")):
            spin.valueChanged.connect(self._commit_board_size)
            form.addRow(text, spin)
        self._board_widgets = (preset, width, height, margin)
        self._vbox.addLayout(form)
        self._vbox.addWidget(_separator())
        self._layout_stats = _dim("")
        self._vbox.addWidget(self._layout_stats)
        self._vbox.addWidget(_separator())
        self._vbox.addWidget(QLabel("Notes"))
        notes = QPlainTextEdit(self.doc.layout.notes)
        notes.setPlaceholderText("Free-form notes saved with the layout…")
        notes.setFixedHeight(90)
        notes.textChanged.connect(lambda: self.doc.set_notes(notes.toPlainText()))
        self._notes_edit = notes
        self._vbox.addWidget(notes)
        self._vbox.addWidget(_separator())
        self._vbox.addWidget(
            _dim(
                "Mouse: drag to move · drag the blue handle to rotate (Shift free, Ctrl 1°, "
                "Alt aims a mirror's reflected beam at the cursor) · wheel zooms · "
                "middle/right drag or Space+drag pans · Ctrl+wheel rotates.\n"
                "Keys: R rotate 90° · Q/E ±15° (Shift ±1°) · F mounting orientation · "
                "M measure · P probe · V select · Del delete · Ctrl+D duplicate · Alt disables snapping."
            )
        )

    # ============================================================= values
    def _component(self) -> Component | None:
        return self.doc.layout.component(self._comp_id) if self._comp_id else None

    def _on_components_changed(self, ids) -> None:
        if self._comp_id in ids:
            self.refresh_values()

    def refresh_values(self) -> None:
        comp = self._component()
        if comp is None:
            return
        for key, spin in self._pose_spins.items():
            value = normalize_angle(comp.angle) if key == "angle" else getattr(comp, key)
            if abs(spin.value() - value) > 10 ** (-spin.decimals()) / 2:
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        if self._label_edit is not None and not self._label_edit.hasFocus():
            self._label_edit.setText(comp.label)
        if self._orientation is not None:
            unit = get_unit_type(comp.type)
            current = comp.orientation or unit.orientations()[0][0]
            index = self._orientation.findData(current)
            self._orientation.blockSignals(True)
            self._orientation.setCurrentIndex(max(index, 0))
            self._orientation.blockSignals(False)
        for key, (field, editor, _label) in self._param_widgets.items():
            value = get_param(comp.params, key, field.default)
            editor.blockSignals(True)
            try:
                if isinstance(editor, QCheckBox):
                    editor.setChecked(bool(value))
                elif isinstance(editor, QComboBox):
                    index = editor.findData(value)
                    editor.setCurrentIndex(max(index, 0))
                elif isinstance(editor, QSpinBox):
                    editor.setValue(int(value or 0))
                elif isinstance(editor, QDoubleSpinBox):
                    auto_box = editor.property("auto_box")
                    if auto_box is not None:
                        auto_box.blockSignals(True)
                        auto_box.setChecked(value is None)
                        auto_box.blockSignals(False)
                        editor.setEnabled(value is not None)
                    shown = value if value is not None else self._auto_value(key, comp)
                    if shown is not None:
                        editor.setValue(float(shown))
            except (TypeError, ValueError):
                pass
            finally:
                editor.blockSignals(False)
        self._update_visibility(comp)

    def _auto_value(self, key: str, comp: Component) -> float | None:
        """What an "Auto" parameter currently resolves to, for display."""
        if key == "optic.thickness_mm":
            try:
                kind = OpticKind(get_param(comp.params, "optic.kind", "mirror"))
            except ValueError:
                return None
            return DEFAULT_THICKNESS_MM.get(kind)
        if key == "fork.angle":
            placed = self.doc.analysis.units.get(comp.id)
            if placed is not None and placed.fork is not None:
                return (placed.fork.angle + 180.0) % 360.0 - 180.0
        return None

    def _refresh_auto_values(self, comp: Component) -> None:
        for key, (_field, editor, _label) in self._param_widgets.items():
            auto_box = editor.property("auto_box") if isinstance(editor, QDoubleSpinBox) else None
            if auto_box is not None and auto_box.isChecked():
                shown = self._auto_value(key, comp)
                if shown is not None:
                    editor.blockSignals(True)
                    editor.setValue(float(shown))
                    editor.blockSignals(False)

    def _update_visibility(self, comp: Component) -> None:
        if self._form is None:
            return
        for field, _editor, label in self._param_widgets.values():
            visible = True
            if field.visible_when is not None:
                other, allowed = field.visible_when
                other_field = self._param_widgets.get(other)
                default = other_field[0].default if other_field else None
                visible = get_param(comp.params, other, default) in allowed
            self._form.setRowVisible(label, visible)

    def refresh_info(self) -> None:
        if self._layout_stats is not None:
            layout = self.doc.layout
            analysis = self.doc.analysis
            n_beams = len(analysis.trace.segments)
            issues = len(analysis.issues)
            text = (
                f"{len(layout.components)} components · {len(layout.measurements)} measurements · "
                f"{len(layout.probes)} probes\n{n_beams} beam segments · "
                + ("no placement problems" if not issues else f"{issues} placement problem(s)")
            )
            self._layout_stats.setText(text)
        comp = self._component()
        if comp is None or self._info is None or self._checks is None:
            return
        self._refresh_auto_values(comp)  # e.g. the fork direction the solver picked
        placed = self.doc.analysis.units.get(comp.id)
        lines: list[str] = []
        board = self.doc.layout.breadboard
        unit = get_unit_type(comp.type)
        if unit.mounting is Mounting.HOLE:
            hole = board.hole_at(comp.x, comp.y)
            lines.append(f"Post holder in hole {hole}" if hole else "Post holder is not on a hole")
        elif unit.mounting is Mounting.PEDESTAL and placed is not None and placed.fork is not None:
            fork = placed.fork
            if fork.hole is not None:
                hole = board.hole_at(*fork.hole)
                lines.append(
                    f"Fork screw in hole {hole}, {fork.screw_distance:.2f}\" out at "
                    f"{normalize_angle(fork.angle + comp.angle):.0f}°"
                )
        if placed is not None:
            if placed.build.elements:
                cx, cy = comp.pose.apply(placed.build.elements[0].center)
                lines.append(f"Optic centre ({cx:.3f}, {cy:.3f}) in")
            lines += list(placed.build.notes)
            lines += self._beam_lines(comp)
        self._info.setText("\n".join(lines))
        issues = self.doc.analysis.issues_for(comp.id)
        self._checks.setText("\n".join("⚠ " + i.message for i in issues))
        self._checks.setVisible(bool(issues))

    def _beam_lines(self, comp: Component) -> list[str]:
        lines = []
        placed = self.doc.analysis.units.get(comp.id)
        if placed is None or not placed.build.elements:
            return lines
        element = placed.build.elements[0].transformed(comp.pose)
        for seg in self.doc.analysis.trace.segments:
            if seg.end_owner != comp.id or seg.end_event == "blocked":
                continue
            d = np.asarray(seg.direction)
            cos_i = abs(float(np.dot(d, element.normal)))
            incidence = math.degrees(math.acos(min(1.0, cos_i)))
            offset = float(np.dot(np.asarray(seg.end) - np.asarray(element.center), element.tangent))
            w = seg.state_at(seg.length).tangential.radius
            lines.append(
                f"Beam hits at {incidence:.1f}° incidence, {offset:+.3f}\" off centre, w {format_beam_size(w)}"
                + (f", {100 * seg.clip:.1f}% passes" if seg.clip < 0.999 else "")
            )
        blocked = [s for s in self.doc.analysis.trace.segments if s.end_owner == comp.id and s.end_event == "blocked"]
        if blocked:
            lines.append(f"{len(blocked)} beam(s) blocked by this mount's body")
        return lines

    # ============================================================ commits
    def _commit_pose(self, key: str, value: float) -> None:
        if self._comp_id:
            self.doc.set_component_fields(self._comp_id, **{key: value})

    def _commit_label(self) -> None:
        comp = self._component()
        if comp is not None and self._label_edit is not None and self._label_edit.text() != comp.label:
            self.doc.set_component_fields(comp.id, merge=False, label=self._label_edit.text().strip())

    def _commit_orientation(self) -> None:
        if self._comp_id and self._orientation is not None:
            self.doc.set_component_fields(self._comp_id, merge=False, orientation=self._orientation.currentData())

    def _commit_param(self, field: ParamField, value) -> None:
        if self._comp_id:
            self.doc.set_component_param(self._comp_id, field.key, value)
            comp = self._component()
            if comp is not None:
                self._update_visibility(comp)

    def _commit_optional(self, field: ParamField, auto: bool, editor: QDoubleSpinBox) -> None:
        editor.setEnabled(not auto)
        self._commit_param(field, None if auto else float(editor.value()))

    def _commit_preset(self, name: str) -> None:
        if name:
            self.doc.set_breadboard(Breadboard.from_preset(name))

    def _commit_board_size(self) -> None:
        if self._board_widgets is None:
            return
        _preset, width, height, margin = self._board_widgets
        board = self.doc.layout.breadboard
        self.doc.set_breadboard(
            Breadboard(
                width=width.value(),
                height=height.value(),
                pitch=board.pitch,
                margin=margin.value(),
                hole_diameter=board.hole_diameter,
                name="Custom",
            )
        )

    def _refresh_board_values(self) -> None:
        if self._board_widgets is None:
            return
        board = self.doc.layout.breadboard
        preset, width, height, margin = self._board_widgets
        for widget, value in ((width, board.width), (height, board.height), (margin, board.margin)):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        index = preset.findData(board.name)
        preset.blockSignals(True)
        preset.setCurrentIndex(index if index >= 0 else preset.count() - 1)
        preset.blockSignals(False)
        notes = self._notes_edit
        if notes is not None and not notes.hasFocus() and notes.toPlainText() != self.doc.layout.notes:
            notes.blockSignals(True)
            notes.setPlainText(self.doc.layout.notes)
            notes.blockSignals(False)
