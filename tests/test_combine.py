"""Smart beamsplitter placement: two beams in, one combined beam out."""

import math

import pytest

from olayout.catalog.units import new_component_params
from olayout.core.layout import Component, Layout
from olayout.engine.analysis import Analyzer
from olayout.engine.combine import CombineError, beam_crossing, plan_combiner, splitter_normal
from olayout.optics.tracer import WarningKind

from conftest import make_component


def _two_beams(t_angle=0.0, r_angle=-90.0, crossing=(10.0, 8.0), back=6.0):
    """Two sources whose beams cross at ``crossing``: T (to transmit) and R (to reflect)."""
    layout = Layout()
    sources = []
    for angle in (t_angle, r_angle):
        rad = math.radians(angle)
        x, y = crossing[0] - back * math.cos(rad), crossing[1] - back * math.sin(rad)
        sources.append(make_component(layout, "laser", x, y, angle))
    return layout, sources


def _plan(layout, t_source, r_source, splitter: Component):
    analyzer = Analyzer()
    result = analyzer.run(layout)
    others = [u for cid, u in result.units.items() if cid != splitter.id]
    trace = Analyzer.trace(others, layout)
    seg_t = next(s for s in trace.segments if s.source == t_source.id and s.parent is None)
    seg_r = next(s for s in trace.segments if s.source == r_source.id and s.parent is None)
    return plan_combiner(splitter, seg_t, seg_r, trace, others, layout, analyzer.cache), trace, seg_t, seg_r


def _new_pellicle() -> Component:
    return Component("bs", "mag-km-pellicle", label="BS1", params=new_component_params("mag-km-pellicle"))


def _combined_outputs(layout, plan):
    result = Analyzer().run(layout)
    return result, [
        s for s in result.trace.segments
        if s.parent is not None and math.dist(s.start, plan.point) < 1e-6
        and s.direction[0] * plan.direction[0] + s.direction[1] * plan.direction[1] > 1 - 1e-9
        and s.end_event == "escaped"
    ]


def test_normal_bisects_the_beams():
    assert splitter_normal((1.0, 0.0), (0.0, -1.0)) == pytest.approx(45.0)
    assert splitter_normal((0.0, 1.0), (-1.0, 0.0)) == pytest.approx(45.0)


@pytest.mark.parametrize("t_angle, r_angle", [(0.0, -90.0), (0.0, 90.0), (90.0, 180.0), (30.0, -60.0), (0.0, -120.0)])
def test_new_pellicle_combines_two_beams(t_angle, r_angle):
    layout, (t, r) = _two_beams(t_angle, r_angle)
    splitter = _new_pellicle()
    plan, _, _, _ = _plan(layout, t, r, splitter)
    assert plan.point == pytest.approx((10.0, 8.0), abs=1e-9)
    assert plan.combined
    assert plan.transmitted == pytest.approx(0.92) and plan.reflected == pytest.approx(0.08)  # BP108 8:92

    splitter.x, splitter.y, splitter.angle, splitter.orientation = plan.x, plan.y, plan.angle, plan.orientation
    layout.components.append(splitter)
    result, outputs = _combined_outputs(layout, plan)
    assert sorted((s.source, round(s.state.power, 6)) for s in outputs) == [(t.id, 0.92), (r.id, 0.08)]
    assert not [w for w in result.trace.warnings if w.kind is WarningKind.CLIPPED]


def test_orientation_is_chosen_so_the_transmitted_beam_leaves_through_the_open_side():
    """A KM100 holding a plate splitter has L-shaped plates: only one way up lets T through."""
    layout, (t, r) = _two_beams(0.0, -90.0)
    splitter = Component("bs", "km-mirror", label="BS1", params=new_component_params("km-mirror"))
    splitter.params["optic"]["kind"] = "splitter"
    plan, _, _, _ = _plan(layout, t, r, splitter)
    assert plan.combined and plan.transmitted == pytest.approx(0.5) and plan.reflected == pytest.approx(0.5)
    for orientation in ("bottom", "side"):  # the other way up really does block it
        splitter.x, splitter.y, splitter.angle, splitter.orientation = plan.x, plan.y, plan.angle, orientation
        layout.components[:] = [c for c in layout.components if c.id != "bs"] + [splitter]
        _, outputs = _combined_outputs(layout, plan)
        assert (len(outputs) == 2) == (orientation == plan.orientation)


def test_parallel_beams_cannot_be_combined():
    layout = Layout()
    t = make_component(layout, "laser", 2.0, 4.0, 0.0)
    r = make_component(layout, "laser", 2.0, 8.0, 0.0)
    with pytest.raises(CombineError, match="parallel"):
        _plan(layout, t, r, _new_pellicle())


def test_crossing_behind_a_source_is_rejected():
    layout, (t, r) = _two_beams(0.0, -90.0, back=6.0)
    r.y -= 7.0  # R now starts below T's path, heading away from it
    with pytest.raises(CombineError, match="behind where it starts"):
        _plan(layout, t, r, _new_pellicle())


def test_crossing_after_a_beam_is_stopped_is_rejected():
    layout, (t, r) = _two_beams(0.0, -90.0)
    make_component(layout, "mirror", 6.0, 8.0, 135.0)  # folds T before it reaches R's path
    with pytest.raises(CombineError, match="stopped or turned aside"):
        _plan(layout, t, r, _new_pellicle())


def test_the_crossing_is_found_past_an_optic_that_does_not_deflect():
    layout, (t, r) = _two_beams(0.0, -90.0)
    window = make_component(layout, "mirror", 6.0, 8.0, 180.0)
    window.params["optic"]["kind"] = "window"
    result = Analyzer().run(layout)
    cx, cy = result.units[window.id].pose.apply(result.units[window.id].build.elements[0].center)
    window.y += 8.0 - cy  # its optic centred on T's path
    trace = Analyzer().run(layout).trace
    seg_t = next(s for s in trace.segments if s.source == t.id and s.parent is None)  # ends on the window
    seg_r = next(s for s in trace.segments if s.source == r.id and s.parent is None)
    assert beam_crossing(trace, seg_t, seg_r) == pytest.approx((10.0, 8.0), abs=1e-9)


def test_hole_mounted_splitter_lands_on_a_hole_and_reports_the_miss():
    layout, (t, r) = _two_beams(0.0, -90.0, crossing=(10.45, 8.56))  # the KM100CP's optic is over its post
    splitter = Component("bs", "km-h-mirror", label="BS1", params=new_component_params("km-h-mirror"))
    splitter.params["optic"]["kind"] = "splitter"
    plan, _, _, _ = _plan(layout, t, r, splitter)
    assert (plan.x, plan.y) == (10.5, 8.5)
    assert plan.off_by == pytest.approx(math.hypot(0.05, 0.06)) and "misses the crossing" in plan.summary()
    assert plan.combined  # parallel, slightly apart: both still leave along T's direction


def test_nearest_crossing_snaps_to_where_beams_cross():
    from olayout.ui.canvas.snapping import nearest_crossing

    layout, _ = _two_beams(0.0, -90.0)
    trace = Analyzer().run(layout).trace
    assert nearest_crossing(trace, (10.1, 8.05), 0.3) == pytest.approx((10.0, 8.0), abs=1e-9)
    assert nearest_crossing(trace, (12.0, 8.0), 0.3) is None  # on a beam, but far from the crossing
