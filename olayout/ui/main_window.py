"""Main window: menus, toolbar, docks, status bar, files, autosave."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QImage, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolBar,
)

from olayout import __version__, paths
from olayout.catalog.units import UNIT_TYPES, Mounting, get_unit_type, has_kb1x1
from olayout.core.breadboard import DEFAULT_PRESET, PRESETS, Breadboard
from olayout.core.layout import FILE_SUFFIX, Component, Layout, LayoutFormatError
from olayout.ui.canvas.tools import PlaceTool
from olayout.ui.canvas.view import LayoutView
from olayout.ui.document import LayoutDocument
from olayout.ui.icons import icon
from olayout.ui.panels.beams import BeamPanel
from olayout.ui.panels.checks import ChecksPanel
from olayout.ui.panels.inspector import InspectorPanel
from olayout.ui.panels.library import LibraryPanel
from olayout.ui.settings import app_settings

log = logging.getLogger(__name__)

FILE_FILTER = f"Optical layouts (*{FILE_SUFFIX} *.json);;All files (*)"
CLIPBOARD_KEY = "olayout-components"
AUTOSAVE_INTERVAL_MS = 15_000
MAX_RECENT = 8

SHORTCUTS_HELP = """
<h3>Mouse</h3>
<table cellspacing=4>
<tr><td><b>Drag part</b></td><td>move (hole-mounted parts jump hole to hole; pedestal parts snap their optic onto beams)</td></tr>
<tr><td><b>Drag blue handle</b></td><td>rotate (15° steps · Shift free · Ctrl 1°)</td></tr>
<tr><td><b>Alt + drag handle</b></td><td>aim a mirror: reflect the incoming beam toward the cursor</td></tr>
<tr><td><b>Alt + drag</b></td><td>move without snapping</td></tr>
<tr><td><b>Wheel</b></td><td>zoom about the cursor · Shift+wheel pan sideways</td></tr>
<tr><td><b>Alt + wheel</b></td><td>rotate the selection 5° (with Shift 1°)</td></tr>
<tr><td><b>Touchpad</b></td><td>two-finger scroll pans · pinch (or Ctrl+scroll) zooms</td></tr>
<tr><td><b>Middle / right drag, Space+drag</b></td><td>pan</td></tr>
<tr><td><b>Right click</b></td><td>context menu (also: ignore a part's placement problems)</td></tr>
<tr><td><b>Hover a beam</b></td><td>beam radius, waist, curvature, power at that point</td></tr>
</table>
<h3>Keys</h3>
<table cellspacing=4>
<tr><td><b>V / M / P</b></td><td>select · measure · probe tool</td></tr>
<tr><td><b>N</b></td><td>place a beam source</td></tr>
<tr><td><b>R / Shift+R</b></td><td>rotate ±90°</td></tr>
<tr><td><b>Q / E</b></td><td>rotate ±15° (with Shift ±1°)</td></tr>
<tr><td><b>F</b></td><td>next mounting orientation (which tap sits on the post)</td></tr>
<tr><td><b>K</b></td><td>lift a mount off its KB1X1 magnetic base, or put it back</td></tr>
<tr><td><b>Arrows</b></td><td>nudge: 1 hole / 1/8" (Shift 1/32", Ctrl 1")</td></tr>
<tr><td><b>Ctrl+D, Ctrl+C/V/X, Del</b></td><td>duplicate, clipboard, delete</td></tr>
<tr><td><b>B / G / L / S</b></td><td>beams · ghost optics (see bases) · labels · snapping</td></tr>
<tr><td><b>Home</b></td><td>fit the breadboard in view</td></tr>
<tr><td><b>Esc</b></td><td>cancel / clear selection</td></tr>
</table>
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = app_settings()
        self.doc = LayoutDocument(self)
        self.view = LayoutView(self.doc)
        self.setCentralWidget(self.view)
        self.setDockOptions(QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.AllowTabbedDocks)
        self.resize(1500, 920)

        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()
        self._connect()

        self._autosaved_revision = -1
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self.autosave)
        self._autosave_timer.start(AUTOSAVE_INTERVAL_MS)

        self._restore_settings()
        self._update_title()
        QTimer.singleShot(0, self.view.fit_board)

    # ================================================================ build
    def _dock(self, title: str, widget, area: Qt.DockWidgetArea, name: str) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(area, dock)
        return dock

    def _build_docks(self) -> None:
        self.library = LibraryPanel()
        self.inspector = InspectorPanel(self.doc)
        self.beam_panel = BeamPanel(self.doc)
        self.checks = ChecksPanel(self.doc)
        self.library_dock = self._dock("Library", self.library, Qt.DockWidgetArea.LeftDockWidgetArea, "library")
        self.inspector_dock = self._dock("Inspector", self.inspector, Qt.DockWidgetArea.RightDockWidgetArea, "inspector")
        self.beam_dock = self._dock("Beams", self.beam_panel, Qt.DockWidgetArea.RightDockWidgetArea, "beams")
        self.checks_dock = self._dock("Checks", self.checks, Qt.DockWidgetArea.RightDockWidgetArea, "checks")
        self.tabifyDockWidget(self.beam_dock, self.checks_dock)
        self.beam_dock.raise_()
        self.resizeDocks([self.library_dock], [270], Qt.Orientation.Horizontal)
        self.resizeDocks([self.inspector_dock], [340], Qt.Orientation.Horizontal)
        self.resizeDocks([self.inspector_dock, self.beam_dock], [520, 360], Qt.Orientation.Vertical)

    def _action(self, text: str, slot, shortcut=None, icon_name: str | None = None, checkable=False, checked=False, tip=""):
        act = QAction(text, self)
        if icon_name:
            act.setIcon(icon(icon_name))
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                act.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                act.setShortcut(QKeySequence(shortcut))
        act.setCheckable(checkable)
        if checkable:
            act.setChecked(checked)
            act.toggled.connect(slot)
        else:
            act.triggered.connect(slot)
        if tip or shortcut:
            keys = shortcut[0] if isinstance(shortcut, (list, tuple)) else shortcut
            act.setToolTip(f"{tip or text.replace('&', '')}" + (f"  ({QKeySequence(keys).toString()})" if keys else ""))
        self.addAction(act)  # shortcuts work even when menus/toolbars are hidden
        return act

    def _build_actions(self) -> None:
        a = self._action
        self.act_new = a("&New…", self.new_layout, QKeySequence.StandardKey.New, "new")
        self.act_open = a("&Open…", self.open_dialog, QKeySequence.StandardKey.Open, "open")
        self.act_save = a("&Save", self.save, QKeySequence.StandardKey.Save, "save")
        self.act_save_as = a("Save &As…", self.save_as, QKeySequence("Ctrl+Shift+S"))
        self.act_export = a("&Export Image…", self.export_image, QKeySequence("Ctrl+E"), "export")
        self.act_quit = a("&Quit", self.close, QKeySequence("Ctrl+Q"))

        self.act_undo = self.doc.undo_stack.createUndoAction(self, "&Undo")
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.setIcon(icon("undo"))
        self.act_redo = self.doc.undo_stack.createRedoAction(self, "&Redo")
        self.act_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.act_redo.setIcon(icon("redo"))
        self.addAction(self.act_undo)
        self.addAction(self.act_redo)

        self.act_cut = a("Cu&t", self.cut, QKeySequence.StandardKey.Cut)
        self.act_copy = a("&Copy", self.copy, QKeySequence.StandardKey.Copy)
        self.act_paste = a("&Paste", self.paste, QKeySequence.StandardKey.Paste)
        self.act_duplicate = a("&Duplicate", self.duplicate, QKeySequence("Ctrl+D"), "duplicate")
        self.act_delete = a("De&lete", self.delete_selection, [QKeySequence.StandardKey.Delete, QKeySequence("Backspace")], "delete")
        self.act_select_all = a("Select &All", self.select_all, QKeySequence.StandardKey.SelectAll)
        self.act_rot90 = a("Rotate +90°", lambda: self.rotate(90), "R", "rotate")
        self.act_rotm90 = a("Rotate −90°", lambda: self.rotate(-90), "Shift+R")
        self.act_rot15 = a("Rotate +15°", lambda: self.rotate(15), "Q")
        self.act_rotm15 = a("Rotate −15°", lambda: self.rotate(-15), "E")
        self.act_rot1 = a("Rotate +1°", lambda: self.rotate(1), "Shift+Q")
        self.act_rotm1 = a("Rotate −1°", lambda: self.rotate(-1), "Shift+E")
        self.act_orient = a("Next Mounting &Orientation", self.cycle_orientation, "F", "flip")
        self.act_lift = a(
            "Lift Off / Put &Back (KB1X1)",
            lambda: self.doc.toggle_lifted_off(self.doc.selection),
            "K",
            tip="Take a mount off its KB1X1 magnetic base (it stops interacting with beams), or put it back",
        )
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, 1), ("Down", 0, -1)):
            a(f"Nudge {key}", lambda dx=dx, dy=dy: self.nudge(dx, dy, "normal"), key)
            a(f"Nudge {key} (fine)", lambda dx=dx, dy=dy: self.nudge(dx, dy, "fine"), f"Shift+{key}")
            a(f"Nudge {key} (1 in)", lambda dx=dx, dy=dy: self.nudge(dx, dy, "coarse"), f"Ctrl+{key}")

        self.tool_group = QActionGroup(self)
        self.act_select = a("&Select / Move", lambda: self.view.set_tool("select"), "V", "select", checkable=False)
        self.act_measure = a("&Measure", lambda: self.view.set_tool("measure"), "M", "measure")
        self.act_probe = a("Beam &Probe", lambda: self.view.set_tool("probe"), "P", "probe")
        for act in (self.act_select, self.act_measure, self.act_probe):
            act.setCheckable(True)
            self.tool_group.addAction(act)
        self.act_select.setChecked(True)
        self.act_source = a("Place Beam &Source", lambda: self.view.start_placing("laser"), "N", "beam")

        self.act_zoom_in = a("Zoom &In", lambda: self.view.zoom_by(1.25, at_cursor=False), [QKeySequence.StandardKey.ZoomIn, QKeySequence("=")])
        self.act_zoom_out = a("Zoom &Out", lambda: self.view.zoom_by(0.8, at_cursor=False), [QKeySequence.StandardKey.ZoomOut, QKeySequence("-")])
        self.act_fit = a("&Fit Breadboard", self.view.fit_board, ["Home", "Ctrl+0"], "fit")
        self.act_beams = a("Show &Beams", lambda on: self._view_option(show_beams=on), "B", "beam", checkable=True, checked=True)
        self.act_bases = a("Show Base &Layer", lambda on: self._view_option(show_bases=on), None, "layers", checkable=True, checked=True)
        self.act_optics = a("Show &Optic Layer", lambda on: self._view_option(show_optics=on), None, checkable=True, checked=True)
        self.act_ghost = a("&Ghost Optic Layer", lambda on: self._view_option(ghost_optics=on), "G", "ghost", checkable=True, tip="Make mounts translucent to see the bases underneath")
        self.act_labels = a("Show &Labels", lambda on: self._view_option(show_labels=on), "L", "labels", checkable=True, checked=True)
        self.act_snap = a("&Snapping", self._toggle_snap, "S", "snap", checkable=True, checked=True, tip="Snap to holes, beams and the 1/8\" grid (hold Alt to bypass)")
        self.beam_scale_group = QActionGroup(self)
        self.beam_scale_actions = []
        for factor in (1, 3, 10):
            act = QAction(f"Beam Width ×{factor}" + (" (true scale)" if factor == 1 else ""), self)
            act.setCheckable(True)
            act.setChecked(factor == 1)
            act.triggered.connect(lambda _c=False, f=factor: self._view_option(beam_exaggeration=float(f)))
            self.beam_scale_group.addAction(act)
            self.beam_scale_actions.append(act)
        self.act_shortcuts = a("&Keyboard && Mouse…", self.show_shortcuts, "F1")
        self.act_about = a("&About", self.show_about)

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addActions([self.act_new, self.act_open])
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self.examples_menu = file_menu.addMenu("Open E&xample")
        for example in sorted(paths.EXAMPLES_DIR.glob(f"*{FILE_SUFFIX}")):
            act = self.examples_menu.addAction(example.name.replace(FILE_SUFFIX, ""))
            act.triggered.connect(lambda _c=False, p=example: self.open_path(p, as_template=True))
        self.examples_menu.setEnabled(bool(self.examples_menu.actions()))
        file_menu.addSeparator()
        file_menu.addActions([self.act_save, self.act_save_as, self.act_export])
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        edit = bar.addMenu("&Edit")
        edit.addActions([self.act_undo, self.act_redo])
        edit.addSeparator()
        edit.addActions([self.act_cut, self.act_copy, self.act_paste, self.act_duplicate, self.act_delete, self.act_select_all])
        edit.addSeparator()
        edit.addActions([self.act_rot90, self.act_rotm90, self.act_rot15, self.act_rotm15, self.act_rot1, self.act_rotm1, self.act_orient, self.act_lift])

        view = bar.addMenu("&View")
        view.addActions([self.act_zoom_in, self.act_zoom_out, self.act_fit])
        view.addSeparator()
        view.addActions([self.act_beams, *self.beam_scale_actions])
        view.addSeparator()
        view.addActions([self.act_bases, self.act_optics, self.act_ghost, self.act_labels])
        view.addSeparator()
        for dock in (self.library_dock, self.inspector_dock, self.beam_dock, self.checks_dock):
            view.addAction(dock.toggleViewAction())

        tools = bar.addMenu("&Tools")
        tools.addActions([self.act_select, self.act_measure, self.act_probe, self.act_source])
        tools.addSeparator()
        tools.addAction(self.act_snap)
        clear = tools.addAction("Clear Measurements && Probes")
        clear.triggered.connect(self.doc.clear_annotations)

        help_menu = bar.addMenu("&Help")
        help_menu.addActions([self.act_shortcuts, self.act_about])
        self._refresh_recent_menu()

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setObjectName("main-toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tb.setIconSize(tb.iconSize() * 0.9)
        tb.addActions([self.act_new, self.act_open, self.act_save])
        tb.addSeparator()
        tb.addActions([self.act_undo, self.act_redo])
        tb.addSeparator()
        tb.addActions([self.act_select, self.act_measure, self.act_probe, self.act_source])
        tb.addSeparator()
        tb.addActions([self.act_rot90, self.act_orient, self.act_duplicate, self.act_delete])
        tb.addSeparator()
        tb.addActions([self.act_snap, self.act_beams, self.act_ghost, self.act_labels, self.act_fit])
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.pos_label = QLabel("x —  y —")
        self.hole_label = QLabel("")
        self.zoom_label = QLabel("")
        self.tool_label = QLabel("Select")
        self.issues_button = QPushButton("")
        self.issues_button.setFlat(True)
        self.issues_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.issues_button.clicked.connect(self._show_checks)
        for widget in (self.pos_label, self.hole_label, self.zoom_label, self.tool_label):
            sb.addWidget(widget)
        sb.addPermanentWidget(self.issues_button)

    def _connect(self) -> None:
        self.doc.fileStateChanged.connect(self._update_title)
        self.doc.analysisUpdated.connect(self._update_issue_status)
        self.view.cursorMoved.connect(self._update_cursor_status)
        self.view.zoomChanged.connect(lambda ppi: self.zoom_label.setText(f"{ppi:.0f} px/in"))
        self.view.toolChanged.connect(self._on_tool_changed)
        self.view.contextMenuRequested.connect(self._context_menu)
        self.library.placeRequested.connect(self._place_from_library)
        self.checks.focusRequested.connect(self.focus_components)
        self.beam_panel.probeFocusRequested.connect(lambda x, y: self.view.center_on_model(x, y))

    # ============================================================== status
    def _update_title(self) -> None:
        star = "*" if self.doc.dirty else ""
        self.setWindowTitle(f"{self.doc.display_name}{star} — Optical Layout")

    def _update_cursor_status(self, x: float, y: float) -> None:
        self.pos_label.setText(f'x {x:8.3f}"   y {y:8.3f}"')
        board = self.doc.layout.breadboard
        hx, hy = board.nearest_hole(x, y)
        hole = board.hole_at(hx, hy)
        near = abs(hx - x) < board.pitch / 2 and abs(hy - y) < board.pitch / 2 and board.contains(x, y)
        self.hole_label.setText(f"hole {hole[0]}, {hole[1]}" if (hole and near) else "")

    def _update_issue_status(self) -> None:
        count = len(self.doc.analysis.active_issues)
        n_ignored = len(self.doc.layout.ignored)
        text = "✓ placement OK" if count == 0 else f"⚠ {count} placement problem(s)"
        self.issues_button.setText(text + (f" ({n_ignored} ignored)" if n_ignored else ""))
        self.issues_button.setStyleSheet("color: #3ecf8e;" if count == 0 else "color: #ff4d5e; font-weight: 600;")

    def _on_tool_changed(self, name: str) -> None:
        self.tool_label.setText({"select": "Select", "measure": "Measure", "probe": "Probe", "place": "Place (Shift-click: keep placing)"}.get(name, name))
        mapping = {"select": self.act_select, "measure": self.act_measure, "probe": self.act_probe}
        act = mapping.get(name)
        if act is not None:
            act.setChecked(True)
        else:
            for a in mapping.values():
                a.setChecked(False)

    def _show_checks(self) -> None:
        self.checks_dock.show()
        self.checks_dock.raise_()

    def _view_option(self, **options) -> None:
        self.view.layout_scene.set_view_options(**options)
        self.settings.setValue("view/" + next(iter(options)), next(iter(options.values())))

    def _toggle_snap(self, on: bool) -> None:
        self.view.snap_settings.enabled = on

    # ================================================================ edit
    def rotate(self, delta: float) -> None:
        self.view.rotate_selection(delta)

    def cycle_orientation(self) -> None:
        if isinstance(self.view.tool, PlaceTool):
            self.view.tool.cycle_orientation()
        else:
            self.doc.cycle_orientation(self.doc.selection)

    def nudge(self, dx: int, dy: int, mode: str) -> None:
        comps = self.doc.selected_components()
        if not comps:
            return
        pitch = self.doc.layout.breadboard.pitch
        on_holes = any(get_unit_type(c.type).mounting is Mounting.HOLE for c in comps)
        if on_holes:
            step = pitch
        else:
            step = {"fine": 1 / 32, "coarse": 1.0}.get(mode, 1 / 8)
        poses = {c.id: (c.x + dx * step, c.y + dy * step, c.angle) for c in comps}
        self.doc.transform_components(poses, "Nudge")

    def delete_selection(self) -> None:
        if self.doc.selection:
            self.doc.delete_components(self.doc.selection)
        elif self.view.layout_scene.selected_annotations:
            self.doc.delete_annotations(self.view.layout_scene.selected_annotations)
            self.view.layout_scene.set_annotation_selection(set())

    def select_all(self) -> None:
        self.doc.set_selection([c.id for c in self.doc.layout.components])

    def copy(self) -> None:
        comps = self.doc.selected_components()
        if comps:
            payload = {CLIPBOARD_KEY: [c.to_dict() for c in comps]}
            QGuiApplication.clipboard().setText(json.dumps(payload, indent=1))

    def cut(self) -> None:
        self.copy()
        self.delete_selection()

    def paste(self) -> None:
        try:
            payload = json.loads(QGuiApplication.clipboard().text())
            comps = [Component.from_dict(d) for d in payload[CLIPBOARD_KEY]]
            for comp in comps:
                get_unit_type(comp.type)
        except Exception:
            self.statusBar().showMessage("Clipboard does not contain layout components", 3000)
            return
        pitch = self.doc.layout.breadboard.pitch
        for comp in comps:
            comp.x += pitch
            comp.y -= pitch
        self.doc.insert_components(comps, "Paste")

    def duplicate(self) -> None:
        comps = [Component.from_dict(c.to_dict()) for c in self.doc.selected_components()]
        if not comps:
            return
        pitch = self.doc.layout.breadboard.pitch
        for comp in comps:
            comp.x += pitch
            comp.y -= pitch
        self.doc.insert_components(comps, "Duplicate")

    def focus_components(self, ids) -> None:
        comps = [c for i in ids if (c := self.doc.layout.component(i)) is not None]
        if not comps:
            return
        self.doc.set_selection([c.id for c in comps])
        self.view.center_on_model(comps[0].x, comps[0].y)

    def _place_from_library(self, type_id: str) -> None:
        self.view.start_placing(type_id)
        self.view.setFocus()

    def _context_menu(self, global_pos, comp_id) -> None:
        self._build_context_menu(comp_id).exec(global_pos)

    def _build_context_menu(self, comp_id) -> QMenu:
        menu = QMenu(self)
        if comp_id is not None:
            menu.addActions([self.act_rot90, self.act_rotm90, self.act_rot15, self.act_rotm15, self.act_orient])
            comp = self.doc.layout.component(comp_id)
            if comp is not None and comp.type in UNIT_TYPES and has_kb1x1(UNIT_TYPES[comp.type]):
                menu.addAction(self.act_lift)
            menu.addSeparator()
            menu.addActions([self.act_duplicate, self.act_copy, self.act_cut, self.act_delete])
            self._add_ignore_actions(menu, comp_id)
        else:
            menu.addActions([self.act_paste, self.act_source, self.act_select_all])
            menu.addSeparator()
            menu.addActions([self.act_fit, self.act_ghost])
        return menu

    def _add_ignore_actions(self, menu: QMenu, comp_id: str) -> None:
        """Accept (or stop accepting) the problems of the component under the cursor."""
        active = self.doc.analysis.active_keys_for(comp_id)
        ignored = [key for key in self.doc.layout.ignored if comp_id in key.components]
        if not active and not ignored:
            return
        menu.addSeparator()
        if active:
            menu.addAction(f"Ignore {len(active)} problem(s) here", lambda: self.doc.set_ignored(active, True))
        if ignored:
            menu.addAction(f"Restore {len(ignored)} ignored problem(s)", lambda: self.doc.set_ignored(ignored, False))

    # =============================================================== files
    def _confirm_discard(self) -> bool:
        if not self.doc.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to {self.doc.display_name}?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def new_layout(self) -> None:
        if not self._confirm_discard():
            return
        names = [p.label for p in PRESETS.values()]
        default = list(PRESETS).index(DEFAULT_PRESET)
        choice, ok = QInputDialog.getItem(self, "New layout", "Breadboard:", names, default, False)
        if not ok:
            return
        preset = list(PRESETS)[names.index(choice)]
        self.doc.new_layout(Breadboard.from_preset(preset))
        self.view.fit_board()

    def open_dialog(self) -> None:
        if not self._confirm_discard():
            return
        start = str(self.doc.path.parent if self.doc.path else paths.EXAMPLES_DIR)
        path, _ = QFileDialog.getOpenFileName(self, "Open layout", start, FILE_FILTER)
        if path:
            self.open_path(Path(path), confirmed=True)

    def open_path(self, path: Path, confirmed: bool = False, as_template: bool = False) -> bool:
        if not confirmed and not self._confirm_discard():
            return False
        try:
            self.doc.open(path)
        except (OSError, LayoutFormatError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot open layout", f"{path}\n\n{exc}")
            return False
        if as_template:  # examples open as untitled copies so they stay pristine
            self.doc.path = None
            self.doc.fileStateChanged.emit()
        else:
            self._add_recent(path)
        self.view.fit_board()
        return True

    def save(self) -> bool:
        if self.doc.path is None:
            return self.save_as()
        return self._save_to(self.doc.path)

    def save_as(self) -> bool:
        start = str(self.doc.path or (Path.home() / f"layout{FILE_SUFFIX}"))
        path, _ = QFileDialog.getSaveFileName(self, "Save layout", start, FILE_FILTER)
        if not path:
            return False
        target = Path(path)
        if not target.name.endswith(".json"):
            target = target.with_name(target.name + FILE_SUFFIX)
        return self._save_to(target)

    def _save_to(self, path: Path) -> bool:
        try:
            self.doc.save(path)
        except OSError as exc:
            QMessageBox.critical(self, "Cannot save layout", f"{path}\n\n{exc}")
            return False
        self._add_recent(path)
        self.statusBar().showMessage(f"Saved {path}", 3000)
        return True

    def export_image(self) -> None:
        start = str((self.doc.path.with_suffix("").with_suffix(".png")) if self.doc.path else Path.home() / "layout.png")
        path, _ = QFileDialog.getSaveFileName(self, "Export image", start, "PNG image (*.png)")
        if not path:
            return
        scene = self.view.layout_scene
        source = scene.board_rect().adjusted(-1.0, -1.0, 1.0, 1.0)
        ppi = 100.0
        image = QImage(int(source.width() * ppi), int(source.height() * ppi), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        hidden = [scene.selection_overlay, scene.hover_label, scene.snap_indicator]
        visible = [item.isVisible() for item in hidden]
        for item in hidden:
            item.setVisible(False)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        scene.render(painter, QRectF(image.rect()), source)
        painter.end()
        for item, was in zip(hidden, visible):
            item.setVisible(was)
        if image.save(path):
            self.statusBar().showMessage(f"Exported {path}", 3000)
        else:
            QMessageBox.critical(self, "Export failed", f"Could not write {path}")

    # -------------------------------------------------------------- recent
    def _recent(self) -> list[str]:
        value = self.settings.value("recent", [])
        if isinstance(value, str):
            value = [value]
        return [v for v in (value or []) if Path(v).exists()]

    def _add_recent(self, path: Path) -> None:
        items = [str(path)] + [p for p in self._recent() if p != str(path)]
        self.settings.setValue("recent", items[:MAX_RECENT])
        self._refresh_recent_menu()

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        for item in self._recent():
            act = self.recent_menu.addAction(item)
            act.triggered.connect(lambda _c=False, p=item: self.open_path(Path(p)))
        self.recent_menu.setEnabled(bool(self.recent_menu.actions()))

    # ------------------------------------------------------------ autosave
    def autosave(self) -> None:
        if self.doc.revision == self._autosaved_revision:
            return
        payload = {"path": str(self.doc.path) if self.doc.path else None, "dirty": self.doc.dirty, "layout": self.doc.snapshot()}
        target = paths.autosave_path()
        try:
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(target)
            self._autosaved_revision = self.doc.revision
        except OSError:
            log.warning("autosave failed", exc_info=True)

    def offer_recovery(self) -> bool:
        """After a crash, offer to restore the autosaved layout."""
        target = paths.autosave_path()
        if not target.exists():
            return False
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            layout = Layout.from_dict(payload["layout"])
        except Exception:
            return False
        if not payload.get("dirty") or not layout.components:
            return False
        answer = QMessageBox.question(
            self,
            "Recover layout",
            "Optical Layout did not shut down cleanly.\nRestore the automatically saved layout?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        original = Path(payload["path"]) if payload.get("path") else None
        self.doc.load_layout(layout, original, dirty=True)
        self.view.fit_board()
        return True

    # ------------------------------------------------------------ settings
    def _restore_settings(self) -> None:
        geometry = self.settings.value("geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        state = self.settings.value("state")
        if isinstance(state, QByteArray):
            self.restoreState(state)
        for key, act in (("show_beams", self.act_beams), ("show_labels", self.act_labels)):
            value = self.settings.value("view/" + key)
            if value is not None:
                act.setChecked(str(value).lower() in ("true", "1"))

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._confirm_discard():
            event.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        try:
            paths.autosave_path().unlink(missing_ok=True)
        except OSError:
            pass
        event.accept()

    # ---------------------------------------------------------------- help
    def show_shortcuts(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Keyboard & mouse")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(SHORTCUTS_HELP)
        box.exec()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Optical Layout",
            f"<h3>Optical Layout {__version__}</h3>"
            "<p>A to-scale 2D layout engine for Thorlabs breadboards with Gaussian beam tracing.</p>"
            "<p>All dimensions in inches. Part models are simplified from Thorlabs drawings; "
            "verify critical clearances against the vendor CAD.</p>",
        )
