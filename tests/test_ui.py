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
