"""Headless UI smoke tests: the window builds, tools work, nothing crashes."""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QMenu  # noqa: E402

from olayout.catalog.parts import Layer  # noqa: E402
from olayout.core.layout import Layout  # noqa: E402
from olayout.ui.main_window import MainWindow  # noqa: E402
from olayout.ui.panels.checks import IGNORED_ROLE  # noqa: E402


@pytest.fixture
def window(qtbot):
    win = MainWindow()
    # pytest-qt closes widgets before fixture teardown; never block on "save changes?".
    win._confirm_discard = lambda: True
    win.resize(1400, 900)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win.view.fit_board()
    return win


def settle(qtbot, win):
    qtbot.wait(5)
    win.doc.analyze_now()
    qtbot.wait(5)


def view_point(win, x, y) -> QPoint:
    return win.view.mapFromScene(QPointF(x, -y))


def test_settings_stay_out_of_the_users_preferences(window):
    import os

    from PySide6.QtCore import QSettings

    from olayout.ui.settings import ENV_VAR

    assert window.settings.format() == QSettings.Format.IniFormat
    assert os.path.normcase(window.settings.fileName()) == os.path.normcase(os.environ[ENV_VAR])


def test_add_components_and_analyze(window, qtbot):
    doc = window.doc
    doc.add_component("laser", 1.0, 3.0)
    doc.add_component("km-mirror", 8.0, 3.0, 135.0)
    doc.add_component("collimator", 2.5, 10.5)
    settle(qtbot, window)
    assert len(doc.analysis.units) == 3
    assert doc.analysis.trace.segments
    scene = window.view.layout_scene
    assert all(items[layer].geometry_key for items in scene.comp_items.values() for layer in items)


def test_drag_moves_hole_mounted_unit_hole_to_hole(window, qtbot):
    doc = window.doc
    cid = doc.add_component("h-mirror", 5.5, 5.5, 0.0)
    settle(qtbot, window)
    vp = window.view.viewport()
    start = view_point(window, 5.5, 5.5)
    end = view_point(window, 8.4, 7.3)
    qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=start)
    for k in range(1, 6):
        qtbot.mouseMove(vp, start + (end - start) * k / 5)
    qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=end)
    comp = doc.layout.component(cid)
    assert (comp.x, comp.y) == (8.5, 7.5)
    assert doc.undo_stack.count() == 2  # add + move
    doc.undo_stack.undo()
    comp = doc.layout.component(cid)
    assert (comp.x, comp.y) == (5.5, 5.5)
    doc.undo_stack.redo()
    assert doc.layout.component(cid).x == 8.5


def test_rotate_delete_duplicate_actions(window, qtbot):
    doc = window.doc
    doc.add_component("km-mirror", 6.0, 6.0, 0.0)
    window.act_rot90.trigger()
    assert doc.selected_components()[0].angle == 90.0
    window.act_rot15.trigger()
    assert doc.selected_components()[0].angle == 105.0
    window.act_duplicate.trigger()
    assert len(doc.layout.components) == 2
    window.act_delete.trigger()
    assert len(doc.layout.components) == 1
    doc.set_selection([doc.layout.components[0].id])
    window.act_orient.trigger()
    assert doc.layout.components[0].orientation == "side"


def test_overlap_is_highlighted(window, qtbot):
    doc = window.doc
    a = doc.add_component("km-mirror", 6.0, 6.0)
    doc.add_component("km-mirror", 6.2, 6.1)
    settle(qtbot, window)
    assert doc.analysis.issues
    items = window.view.layout_scene.comp_items[a]
    assert any(item.illegal for item in items.values())
    assert "problem" in window.issues_button.text()


def test_save_open_round_trip(window, qtbot, tmp_path):
    doc = window.doc
    doc.add_component("laser", 1.0, 3.0)
    doc.add_measurement((1.0, 1.0), (4.0, 5.0))
    path = tmp_path / "t.olayout.json"
    assert window._save_to(path)
    assert not doc.dirty
    saved = Layout.load(path).to_dict()
    doc.new_layout()
    assert window.open_path(path, confirmed=True)
    assert doc.layout.to_dict() == saved


def test_measure_and_probe_tools(window, qtbot):
    doc = window.doc
    doc.add_component("laser", 1.0, 3.0)
    settle(qtbot, window)
    vp = window.view.viewport()
    window.act_measure.trigger()
    a, b = view_point(window, 2.5, 6.5), view_point(window, 6.5, 6.5)
    qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=a)
    qtbot.mouseMove(vp, b)
    qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)
    assert len(doc.layout.measurements) == 1
    (m,) = doc.layout.measurements
    assert m.a == pytest.approx((2.5, 6.5)) and m.b == pytest.approx((6.5, 6.5))  # snapped to holes
    window.act_probe.trigger()
    p = view_point(window, 5.0, 3.02)
    qtbot.mouseMove(vp, p)
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=p)
    assert len(doc.layout.probes) == 1
    assert doc.layout.probes[0].y == pytest.approx(3.0)
    settle(qtbot, window)
    assert window.beam_panel.table.rowCount() == 1


def test_place_tool_and_export(window, qtbot, tmp_path, monkeypatch):
    doc = window.doc
    window.view.start_placing("mirror")
    vp = window.view.viewport()
    p = view_point(window, 10.3, 9.1)
    qtbot.mouseMove(vp, p)
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=p)
    assert len(doc.layout.components) == 1
    assert window.view.tool.name == "select"
    out = tmp_path / "shot.png"
    monkeypatch.setattr("olayout.ui.main_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(out), ""))
    window.export_image()
    assert out.exists() and out.stat().st_size > 1000


def test_inspector_shows_auto_values(window, qtbot):
    doc = window.doc
    doc.add_component("km-mirror", 6.3, 6.2)
    settle(qtbot, window)
    inspector = window.inspector
    _, thickness, _ = inspector._param_widgets["optic.thickness_mm"]
    assert not thickness.isEnabled() and thickness.value() == pytest.approx(6.0)  # typical mirror
    _, fork, _ = inspector._param_widgets["fork.angle"]
    solved = doc.analysis.units[doc.selection[0]].fork.angle
    assert fork.value() == pytest.approx((solved + 180) % 360 - 180, abs=0.05)


def test_inspector_edits_parameters(window, qtbot):
    doc = window.doc
    cid = doc.add_component("lens", 6.0, 6.0)
    inspector = window.inspector
    field, editor, _ = inspector._param_widgets["optic.focal_length_mm"]
    editor.setValue(250.0)
    assert doc.layout.component(cid).params["optic"]["focal_length_mm"] == 250.0
    inspector._pose_spins["x"].setValue(7.25)
    assert doc.layout.component(cid).x == 7.25


def wheel(win, pos: QPoint, delta: int, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    """Send one mouse-wheel step (delta in 1/8 degree; 120 = one notch) at a viewport position."""
    global_pos = QPointF(win.view.viewport().mapToGlobal(pos))
    event = QWheelEvent(
        QPointF(pos), global_pos, QPoint(0, 0), QPoint(0, delta), Qt.MouseButton.NoButton, modifiers,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    win.view.wheelEvent(event)


def test_wheel_zoom_keeps_the_point_under_the_cursor(window, qtbot):
    view = window.view
    pos = QPoint(25, 20)  # near a viewport corner, off the board when zoomed out
    for delta in (120, 120, -120, 480, -960, -960, -960):
        before = view.mapToScene(pos)
        wheel(window, pos, delta)
        after = view.mapToScene(pos)
        tolerance = 1.5 / view.pixels_per_inch()  # within a pixel and a half
        assert abs(after.x() - before.x()) < tolerance and abs(after.y() - before.y()) < tolerance


def test_pan_is_exact_even_when_zoomed_far_out(window, qtbot):
    view = window.view
    centre = QPointF(view.viewport().width() / 2, view.viewport().height() / 2)
    view.zoom_at(1e-6, centre)  # clamps at the minimum zoom
    ppi = view.pixels_per_inch()
    before = view.mapToScene(centre.toPoint())
    view.scroll_by(150.0, -90.0)
    after = view.mapToScene(centre.toPoint())
    assert (after.x() - before.x()) * ppi == pytest.approx(150.0, abs=1.0)
    assert (after.y() - before.y()) * ppi == pytest.approx(-90.0, abs=1.0)


def test_alt_wheel_rotates_the_selection_and_ctrl_wheel_zooms(window, qtbot):
    doc, view = window.doc, window.view
    cid = doc.add_component("km-mirror", 6.0, 6.0)
    doc.set_selection([cid])
    pos = view_point(window, 6.0, 6.0)
    start, ppi = doc.layout.component(cid).angle, view.pixels_per_inch()
    wheel(window, pos, 120, Qt.KeyboardModifier.ControlModifier)
    assert view.pixels_per_inch() > ppi and doc.layout.component(cid).angle == start
    wheel(window, pos, 120, Qt.KeyboardModifier.AltModifier)
    assert doc.layout.component(cid).angle == pytest.approx(start + 5.0)
    wheel(window, pos, -120, Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)
    assert doc.layout.component(cid).angle == pytest.approx(start + 4.0)


def test_ignoring_problems_clears_highlights_and_can_be_undone(window, qtbot):
    doc, panel = window.doc, window.checks
    a = doc.add_component("km-mirror", 6.0, 6.0)
    doc.add_component("km-mirror", 6.2, 6.1)
    settle(qtbot, window)
    assert doc.analysis.active_issues and not panel.ignore_button.isEnabled()

    panel.list.selectAll()
    assert panel.ignore_button.isEnabled()
    panel.ignore_button.click()
    settle(qtbot, window)
    assert doc.layout.ignored and doc.analysis.active_issues == []
    assert not any(item.illegal for item in window.view.layout_scene.comp_items[a].values())
    assert "OK" in window.issues_button.text() and "ignored" in window.issues_button.text()
    assert all(panel.list.item(i).data(IGNORED_ROLE) for i in range(panel.list.count()))  # still listed
    assert not panel.restore_all_button.isHidden()  # the dock tab itself may be hidden

    doc.undo_stack.undo()
    settle(qtbot, window)
    assert not doc.layout.ignored and doc.analysis.active_issues


def test_context_menu_ignores_and_restores_a_parts_problems(window, qtbot):
    doc = window.doc
    a = doc.add_component("km-mirror", 6.0, 6.0)
    doc.add_component("km-mirror", 6.2, 6.1)
    settle(qtbot, window)
    menu = QMenu()
    window._add_ignore_actions(menu, a)
    (ignore,) = [act for act in menu.actions() if act.text().startswith("Ignore")]
    ignore.trigger()
    settle(qtbot, window)
    assert doc.analysis.issues and not doc.analysis.issues_for(a)
    menu = QMenu()
    window._add_ignore_actions(menu, a)
    (restore,) = [act for act in menu.actions() if act.text().startswith("Restore")]
    restore.trigger()
    settle(qtbot, window)
    assert doc.analysis.issues_for(a)


def test_inspector_reports_each_mirror_of_a_beam_expander(window, qtbot):
    doc = window.doc
    doc.add_component("laser", 2.0, 10.0)
    bx = doc.add_component("beam-expander", 8.0, 10.0)
    doc.set_selection([bx])
    settle(qtbot, window)
    info = window.inspector._info.text()
    assert "Mirror 1: hit at 21.7° incidence" in info and "Mirror 2: hit at 16.9° incidence" in info


def test_inspector_reports_how_close_beams_come_to_a_knife_edge(window, qtbot):
    doc = window.doc
    knife = doc.add_component("knife-edge", 10.0, 9.0, 180.0)
    w = 1.0 / 25.4  # the beams' 1 mm waist
    for y, angle in ((12.0, -90.0), (6.0, 90.0)):  # 2 w behind the edge, from either side
        doc.add_component("laser", 10.0 + 2 * w, y, angle, params={"waist_um": 1000.0})
    doc.set_selection([knife])
    settle(qtbot, window)
    info = window.inspector._info.text()
    assert "Optic centre" not in info  # two legs: each beam is listed instead
    assert "Left leg: hit at 45.0° incidence" in info and "Right leg: hit at 45.0° incidence" in info
    assert info.count("2.0 w from the edge") == 2


def test_k_lifts_a_mount_off_its_kb1x1_and_puts_it_back(window, qtbot):
    doc = window.doc
    m = doc.add_component("kb-mirror", 6.0, 6.0)
    plain = doc.add_component("km-mirror", 12.0, 6.0)
    doc.set_selection([m, plain])
    settle(qtbot, window)
    window.act_lift.trigger()
    settle(qtbot, window)
    assert doc.layout.component(m).params["kb1x1"]["seated"] is False
    assert "kb1x1" not in doc.layout.component(plain).params  # no KB1X1: untouched
    optic_item = window.view.layout_scene.comp_items[m][Layer.OPTIC]
    assert optic_item._detached and optic_item.has_content()  # drawn faintly
    window.act_lift.trigger()
    settle(qtbot, window)
    assert doc.layout.component(m).params["kb1x1"]["seated"] is True
    doc.undo_stack.undo()
    settle(qtbot, window)
    assert doc.layout.component(m).params["kb1x1"]["seated"] is False
    # The right-click menu offers it only for KB1X1 units.
    assert window.act_lift in window._build_context_menu(m).actions()
    assert window.act_lift not in window._build_context_menu(plain).actions()


def test_combine_tool_places_a_pellicle_where_two_beams_cross(window, qtbot):
    doc = window.doc
    doc.add_component("laser", 3.0, 8.0, 0.0)  # T, to transmit
    doc.add_component("laser", 10.0, 15.0, 270.0)  # R, to reflect onto it
    doc.set_selection([])
    settle(qtbot, window)
    vp = window.view.viewport()
    window.act_combine.trigger()
    assert window.view.tool.name == "combine" and "transmit" in window.tool_label.text()
    t, r = view_point(window, 6.0, 8.0), view_point(window, 10.0, 12.0)
    qtbot.mouseMove(vp, t)
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=t)
    assert "reflect onto it" in window.tool_label.text()
    qtbot.mouseMove(vp, r)  # previews the plan: ghost splitter + combined beam
    tool = window.view.tool
    assert tool.output_line.isVisible() and any(item.isVisible() for item in tool.ghost.values())
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=r)
    assert window.view.tool.name == "select"
    (bs,) = [c for c in doc.layout.components if c.type == "mag-km-pellicle"]
    assert doc.selection == [bs.id]
    settle(qtbot, window)
    combined = [s for s in doc.analysis.trace.segments if s.parent is not None and s.direction[0] > 0.999999
                and abs(s.start[1] - 8.0) < 1e-6 and abs(s.start[0] - 10.0) < 1e-6]
    assert len({s.source for s in combined}) == 2  # both beams leave the splitter along T
    doc.undo_stack.undo()
    assert not [c for c in doc.layout.components if c.type == "mag-km-pellicle"]


def test_combine_tool_moves_the_selected_splitter_and_esc_backs_out(window, qtbot):
    doc = window.doc
    doc.add_component("laser", 3.0, 8.0, 0.0)
    doc.add_component("laser", 10.0, 15.0, 270.0)
    bs = doc.add_component("mag-km-pellicle", 15.0, 3.0, 0.0)
    settle(qtbot, window)
    assert window.act_combine in window._build_context_menu(bs).actions()  # offered for splitters
    assert window.inspector._combine is not None and not window.inspector._combine.isHidden()
    window.inspector._combine.click()
    assert window.view.tool.name == "combine" and window.view.tool.existing
    vp = window.view.viewport()
    t = view_point(window, 6.0, 8.0)
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=t)
    qtbot.keyClick(vp, Qt.Key.Key_Escape)  # forget the first beam...
    assert window.view.tool.name == "combine" and window.view.tool.first is None
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=t)
    r = view_point(window, 10.0, 12.0)
    qtbot.mouseMove(vp, r)
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=r)
    assert len(doc.layout.components) == 3  # moved, not added
    settle(qtbot, window)
    element = doc.analysis.units[bs].build.elements[0]
    assert doc.layout.component(bs).pose.apply(element.center) == pytest.approx((10.0, 8.0), abs=1e-9)
    window.act_combine.trigger()
    qtbot.keyClick(vp, Qt.Key.Key_Escape)  # ...and Esc again leaves the tool
    assert window.view.tool.name == "select"
    mirror = doc.add_component("km-mirror", 4.0, 4.0)
    settle(qtbot, window)
    assert window.act_combine not in window._build_context_menu(mirror).actions()
    assert window.inspector._combine.isHidden()


def test_thumbnails_frame_the_component_not_its_clamping_fork():
    from olayout.catalog.units import Mounting, default_fork, get_unit_type, new_component_params
    from olayout.core.layout import Component
    from olayout.engine.world import BuildCache
    from olayout.ui.canvas.painting import parts_bounds, thumbnail_framing, unit_thumbnail

    def framed_size(type_id):
        unit = get_unit_type(type_id)
        fork = default_fork() if unit.mounting is Mounting.PEDESTAL else None
        placed = BuildCache().place(Component("t", type_id, params=new_component_params(type_id)), fork)
        framed = thumbnail_framing(list(placed.parts))
        assert {p.layer for p in framed} == {Layer.OPTIC}  # the mount itself, not what holds it
        rect = parts_bounds(framed)
        return rect.width(), rect.height()

    # The same mount on a pedestal or in a hole: framed identically.
    for pedestal, hole in (("mirror", "h-mirror"), ("objective", "h-objective"), ("lens", "h-lens")):
        assert framed_size(pedestal) == pytest.approx(framed_size(hole))
    assert unit_thumbnail("base", 40).width() == 40  # a bare base has nothing else: framed whole


def test_rails_and_boards_are_drawn_end_to_end(window, qtbot):
    doc = window.doc
    vp = window.view.viewport()
    window.view.start_placing("tslot-rail")
    assert "drag from one end to the other" in window.tool_label.text()
    a, b = view_point(window, 2.5, 2.5), view_point(window, 2.5, 9.5)  # hole to hole
    qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=a)
    for k in range(1, 6):
        qtbot.mouseMove(vp, a + (b - a) * k / 5)
    qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)
    (rail,) = doc.layout.components
    assert (rail.x, rail.y, rail.angle) == pytest.approx((2.5, 2.5, 90.0))
    assert rail.params["rail"]["length_in"] == pytest.approx(7.0)
    assert window.view.tool.name == "place"  # stays on for the next member

    c = view_point(window, 6.2, 4.1)  # a click: the default length, along the current angle
    qtbot.mouseMove(vp, c)
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=c)
    second = doc.layout.components[1]
    assert second.params["rail"]["length_in"] == pytest.approx(12.0)
    assert rail.params["rail"]["length_in"] == pytest.approx(7.0)  # the first one is untouched
    qtbot.keyClick(vp, Qt.Key.Key_Escape)
    assert window.view.tool.name == "select"
    settle(qtbot, window)
    assert not doc.analysis.issues  # framing joins up without collisions


def test_drawn_members_snap_to_each_others_ends(window, qtbot):
    doc = window.doc
    rail = doc.add_component("tslot-rail", 3.13, 3.13, 0.0)  # off the hole grid, ending at (15.13, 3.13)
    settle(qtbot, window)
    vp = window.view.viewport()
    window.view.start_placing("board")
    a, b = view_point(window, 15.1, 3.15), view_point(window, 15.13, 8.0)
    qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=a)
    qtbot.mouseMove(vp, b)
    qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)
    board = next(c for c in doc.layout.components if c.type == "board")
    assert (board.x, board.y) == pytest.approx((15.13, 3.13))  # on the rail's far end
    assert board.angle == pytest.approx(90.0) and doc.layout.component(rail).type == "tslot-rail"


def test_stage_thumbnail_frames_the_stage():
    from olayout.catalog.units import new_component_params
    from olayout.core.layout import Component
    from olayout.engine.world import BuildCache
    from olayout.ui.canvas.painting import thumbnail_framing

    placed = BuildCache().place(Component("t", "sample-stage", params=new_component_params("sample-stage")))
    assert {p.part_id for p in thumbnail_framing(list(placed.parts))} == {"MAX311D", "SAMPLE"}
