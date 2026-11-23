"""Headless UI smoke tests: the window builds, tools work, nothing crashes."""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402

from olayout.core.layout import Layout  # noqa: E402
from olayout.ui.main_window import MainWindow  # noqa: E402


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
