"""Aiming a beam: turn the component it leaves so it passes through a point."""

import math

import pytest

from olayout.core.layout import Layout
from olayout.engine.aim import AimError, beam_key, find_beam, leaving_component, plan_aim, turn_toward
from olayout.engine.analysis import Analyzer

from conftest import make_component


def leaving(result, comp, port=None):
    """The beam leaving ``comp`` (through ``port``, if given)."""
    trace = result.trace
    for seg in trace.segments:
        if leaving_component(trace, seg) == comp.id:
            steps = beam_key(trace, seg).steps
            if port is None or steps[-1:] == ((comp.id, port),):
                return seg
    raise AssertionError(f"no beam leaves {comp.label}")


def aim(layout, comp, target, port=None):
    """Plan, apply and re-trace: returns (plan, how far the beam then passes from the target)."""
    analyzer = Analyzer()
    result = analyzer.run(layout)
    seg = leaving(result, comp, port)
    others = [u for cid, u in result.units.items() if cid != comp.id]
    plan = plan_aim(comp, seg, target, result.trace, others, layout, analyzer.cache)
    key = beam_key(result.trace, seg)
    comp.angle = plan.angle
    after = find_beam(Analyzer().run(layout).trace, key)
    vx, vy = target[0] - after.start[0], target[1] - after.start[1]
    assert vx * after.direction[0] + vy * after.direction[1] > 0  # ahead of it, not behind
    return plan, abs(after.direction[0] * vy - after.direction[1] * vx)


def test_a_source_turns_to_point_at_a_hole():
    layout = Layout()
    source = make_component(layout, "laser", 2.0, 2.0, 0.0)
    plan, miss = aim(layout, source, (10.5, 6.5))
    assert miss < 1e-6 and plan.angle == pytest.approx(math.degrees(math.atan2(4.5, 8.5)), abs=1e-6)
    assert plan.turn == pytest.approx(plan.angle) and not plan.stopped_by


def test_a_mirror_ahead_of_its_post_is_aimed_exactly():
    """A KM100's mirror is ~0.57" ahead of the post: turning it also moves where the beam lands."""
    layout = Layout()
    make_component(layout, "laser", 2.0, 4.9, 0.0)
    mirror = make_component(layout, "km100", 8.5, 4.5, 135.0)
    plan, miss = aim(layout, mirror, (6.5, 12.5), port="reflected")
    assert miss < 1e-6 and 0 < plan.turn < 10
    assert "turns +" in plan.summary("M1") and "(6.500, 12.500)" in plan.summary("M1")


def test_a_collimator_sends_its_beam_through_a_point():
    layout = Layout()
    collimator = make_component(layout, "collimator", 2.5, 2.5, 0.0)
    _, miss = aim(layout, collimator, (15.5, 6.5), port="transmitted")
    assert miss < 1e-6


def test_the_reflected_port_of_a_splitter_can_be_aimed_but_not_the_transmitted_one():
    layout = Layout()
    make_component(layout, "laser", 2.0, 4.9, 0.0)
    splitter = make_component(layout, "km100", 8.5, 4.5, 135.0)
    splitter.params["optic"]["kind"] = "splitter"
    _, miss = aim(layout, splitter, (9.5, 13.5), port="reflected")
    assert miss < 1e-6
    with pytest.raises(AimError, match="cannot send this beam"):
        aim(layout, splitter, (15.5, 8.5), port="transmitted")  # turning only shifts it sideways


def test_a_point_out_of_reach_is_explained():
    layout = Layout()
    make_component(layout, "laser", 2.0, 4.9, 0.0)
    mirror = make_component(layout, "km100", 8.5, 4.5, 135.0)
    with pytest.raises(AimError, match="misses its optic or hits the mount"):
        aim(layout, mirror, (4.5, 1.5))  # nearly back where it came from


def test_something_in_the_way_is_reported():
    layout = Layout()
    source = make_component(layout, "laser", 2.0, 2.5, 0.0)
    make_component(layout, "beam-dump", 6.5, 4.5, 180.0)  # on the way to the point
    plan, miss = aim(layout, source, (12.5, 7.5))
    assert miss < 1e-6 and plan.stopped_by == "BB1"


def test_the_quick_solve_follows_the_cursor():
    layout = Layout()
    make_component(layout, "laser", 2.0, 4.9, 0.0)
    mirror = make_component(layout, "km100", 8.5, 4.5, 135.0)
    analyzer = Analyzer()
    result = analyzer.run(layout)
    seg = leaving(result, mirror, "reflected")
    angle = turn_toward(mirror, seg, (7.0, 12.0), result.trace, analyzer.cache)
    assert angle is not None and 130 < angle < 140
    assert turn_toward(mirror, seg, (4.5, 1.5), result.trace, analyzer.cache) is None  # out of reach


# ------------------------------------------------- through other optics
def after(result, *comps):
    """The beam after it met ``comps`` in this order."""
    ids = [c.id for c in comps]
    for seg in result.trace.segments:
        if [s.end_owner for s in result.trace.path_to(seg)[:-1]] == ids:
            return seg
    raise AssertionError("no such beam")


def aim_after(layout, turned, target, *comps):
    """Turn ``turned`` so the beam after ``comps`` passes ``target``; returns (plan, miss after applying)."""
    analyzer = Analyzer()
    result = analyzer.run(layout)
    seg = after(result, *comps)
    others = [u for cid, u in result.units.items() if cid != turned.id]
    plan = plan_aim(turned, seg, target, result.trace, others, layout, analyzer.cache)
    key = beam_key(result.trace, seg)
    turned.angle = plan.angle
    final = find_beam(Analyzer().run(layout).trace, key)
    vx, vy = target[0] - final.start[0], target[1] - final.start[1]
    return plan, abs(final.direction[0] * vy - final.direction[1] * vx)


def periscope():
    """Laser -> M1 (up) -> M2 (right) -> M3 (down), mirrors over their posts."""
    layout = Layout()
    source = make_component(layout, "laser", 1.5, 2.5, 0.0)
    m1 = make_component(layout, "km100cp", 6.5, 2.5, 135.0)
    m2 = make_component(layout, "km100cp", 6.5, 8.5, 315.0)
    m3 = make_component(layout, "km100cp", 14.5, 8.5, 225.0)
    return layout, source, m1, m2, m3


def test_a_part_is_turned_to_aim_the_beam_after_other_mirrors():
    layout, source, m1, m2, m3 = periscope()
    angles = (m2.angle, m3.angle)
    plan, miss = aim_after(layout, m1, (14.375, 4.0), m1, m2, m3)
    assert miss < 1e-6 and plan.via == ("M2", "M3") and (m2.angle, m3.angle) == angles  # only M1 turns
    assert len(plan.path) == 4 and plan.path[-1] == (14.375, 4.0) and "via M2, M3" in plan.summary("M1")
    plan, miss = aim_after(layout, source, (14.75, 3.5), m1, m2, m3)  # the source itself, via all three
    assert miss < 1e-6 and plan.via == ("M1", "M2", "M3")


def test_a_beam_sliding_off_a_mirror_on_the_way_is_explained():
    layout, _, m1, m2, m3 = periscope()
    with pytest.raises(AimError, match="can point the beam there, but the beam would miss M2"):
        aim_after(layout, m1, (10.5, 2.5), m1, m2, m3)


def test_splitters_and_windows_on_the_way_are_fine():
    layout = Layout()
    make_component(layout, "laser", 1.5, 2.5, 0.0)
    m1 = make_component(layout, "km100cp", 6.5, 2.5, 135.0)
    bs = make_component(layout, "pellicle-bp107", 6.5, 6.5, 315.0)
    iris = make_component(layout, "iris", 6.5, 10.5, 90.0)
    iris.params["iris"] = {"aperture_mm": 25.0}
    plan, miss = aim_after(layout, m1, (6.7, 15.5), m1, bs, iris)  # through the pellicle and the iris
    assert miss < 1e-6 and plan.via == ("BS1", "I1")
    plan, miss = aim_after(layout, m1, (14.5, 6.9), m1, bs)  # off the pellicle
    assert miss < 1e-6 and plan.via == ("BS1",)


@pytest.mark.parametrize("between", ["beam-expander", "lens"])
def test_focusing_optics_on_the_way_are_refused(between):
    layout = Layout()
    make_component(layout, "laser", 1.5, 2.5, 0.0)
    m1 = make_component(layout, "km100cp", 6.5, 2.5, 135.0)
    other = make_component(layout, between, 6.5, 6.5 if between == "lens" else 6.0, 90.0)
    result = Analyzer().run(layout)
    deepest = max(result.trace.segments, key=lambda s: s.depth)
    assert other.id in {s.end_owner for s in result.trace.path_to(deepest)}
    others = [u for cid, u in result.units.items() if cid != m1.id]
    with pytest.raises(AimError, match="only flat mirrors, splitters and windows"):
        plan_aim(m1, deepest, (8.0, 15.0), result.trace, others, layout, Analyzer().cache)


def test_a_beam_must_come_from_the_part_that_turns():
    layout, _, m1, m2, m3 = periscope()
    result = Analyzer().run(layout)
    with pytest.raises(AimError, match="does not come from M3"):
        plan_aim(m3, after(result, m1, m2), (12.5, 8.5), result.trace, [], layout, Analyzer().cache)


def test_many_mirrors_on_the_way_cost_no_more_and_stay_exact():
    import time

    layout = Layout()
    make_component(layout, "laser", 1.5, 1.5, 0.0)
    pattern = [((0, 0), 135.0), ((0, 3), 315.0), ((2, 3), 225.0), ((2, 0), 45.0)]  # a zig-zag
    mirrors = [
        make_component(layout, "km100cp", 3.5 + 4 * (k // 4) + dx, 1.5 + dy, angle)
        for k, ((dx, dy), angle) in ((k, pattern[k % 4]) for k in range(9))
    ]
    end = after(Analyzer().run(layout), *mirrors).point_at(3.0)
    target = (end[0] + 0.02, end[1] - 0.04)
    start = time.perf_counter()
    plan, miss = aim_after(layout, mirrors[0], target, *mirrors)
    assert len(plan.via) == 8 and miss < 1e-6
    assert time.perf_counter() - start < 0.5  # ~3 ms for the solve itself
