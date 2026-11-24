import math

import pytest
from shapely.geometry import box

from olayout.core.units import MM, NM, UM
from olayout.optics.elements import Blocker, Emitter, OpticalElement, OpticKind
from olayout.optics.tracer import BeamTracer

BOUNDS = (-50.0, -50.0, 50.0, 50.0)


def emitter(x=0.0, y=0.0, angle=0.0, waist=500 * UM, power=1.0):
    return Emitter(position=(x, y), angle=angle, wavelength=633 * NM, waist_radius=waist, power=power, owner="src")


def mirror(x, y, normal_angle, **kw):
    return OpticalElement(OpticKind.MIRROR, (x, y), normal_angle, 0.45, owner="m", **kw)


def test_beam_escapes_without_optics():
    result = BeamTracer([], [], BOUNDS).trace([emitter()])
    (seg,) = result.segments
    assert seg.end_event == "escaped"
    assert seg.length == pytest.approx(50.0)


def test_45_degree_mirror_folds_beam():
    result = BeamTracer([mirror(10, 0, 135.0)], [], BOUNDS).trace([emitter()])
    first, second = result.segments
    assert first.end_event == "reflect"
    assert first.length == pytest.approx(10.0)
    assert second.direction == pytest.approx((0.0, 1.0), abs=1e-12)
    assert second.start == pytest.approx((10.0, 0.0))
    assert second.path_start == pytest.approx(10.0)


def test_mirror_back_side_is_opaque():
    result = BeamTracer([mirror(10, 0, 0.0)], [], BOUNDS).trace([emitter()])  # normal faces +x: beam hits its back
    (seg,) = result.segments
    assert seg.end_event == "blocked"


def test_blocker_stops_beam():
    blocker = Blocker(box(5, -1, 6, 1), owner="body")
    result = BeamTracer([mirror(10, 0, 135.0)], [blocker], BOUNDS).trace([emitter()])
    (seg,) = result.segments
    assert seg.end_event == "blocked"
    assert seg.end_owner == "body"
    assert seg.length == pytest.approx(5.0)


def test_lens_focuses_at_focal_length():
    f = 100 * MM
    lens = OpticalElement(OpticKind.LENS, (2.0, 0.0), 180.0, 0.45, focal_length=f, owner="lens")
    result = BeamTracer([lens], [], BOUNDS).trace([emitter(waist=2 * MM)])
    after = result.segments[1]
    waist_s = after.waist_positions()
    assert waist_s and waist_s[0] == pytest.approx(f, rel=2e-3)


def test_off_axis_lens_steers_beam():
    f = 4.0
    h = 0.2
    lens = OpticalElement(OpticKind.LENS, (5.0, 0.0), 0.0, 0.45, focal_length=f, owner="lens")
    result = BeamTracer([lens], [], BOUNDS).trace([emitter(y=h)])
    after = result.segments[1]
    slope = after.direction[1] / after.direction[0]
    assert slope == pytest.approx(-h / f, rel=1e-9)


def test_splitter_divides_power():
    bs = OpticalElement(OpticKind.SPLITTER, (5.0, 0.0), 135.0, 0.45, reflectance=0.3, owner="bs")
    result = BeamTracer([bs], [], BOUNDS).trace([emitter(power=2.0)])
    children = [s for s in result.segments if s.parent == 0]
    powers = sorted(s.state.power for s in children)
    assert powers == pytest.approx([0.6, 1.4])
    directions = sorted(tuple(round(c, 9) for c in s.direction) for s in children)
    assert directions == [(0.0, 1.0), (1.0, 0.0)]


def test_clipping_is_reported():
    small = OpticalElement(OpticKind.MIRROR, (5.0, 0.0), 135.0, 0.05, owner="tiny")
    result = BeamTracer([small], [], BOUNDS).trace([emitter(waist=2 * MM)])
    assert result.segments[0].clip < 0.99
    assert any("clipped" in w.message for w in result.warnings)


def test_oblique_incidence_widens_the_footprint():
    """A 45° mirror sees the beam 1/cos 45° wider: the same edge clips it more than at normal incidence."""
    w = 2 * MM
    half = 0.08
    normal = OpticalElement(OpticKind.MIRROR, (5.0, 0.0), 180.0, half, owner="n")
    tilted = OpticalElement(OpticKind.MIRROR, (5.0, 0.0), 135.0, half, owner="t")
    straight_on = BeamTracer([normal], [], BOUNDS).trace([emitter(waist=w)]).segments[0].clip
    at_45 = BeamTracer([tilted], [], BOUNDS).trace([emitter(waist=w)]).segments[0].clip
    # The beam barely grows over 5" (Rayleigh range ~20 m), so its waist is its size at the mirror.
    assert straight_on == pytest.approx(math.erf(math.sqrt(2) * half / w), rel=1e-3)
    assert at_45 == pytest.approx(math.erf(math.sqrt(2) * half * math.cos(math.radians(45)) / w), rel=1e-3)


def test_curved_mirror_is_astigmatic_off_axis():
    curved = mirror(10, 0, 135.0, roc=20.0)
    result = BeamTracer([curved], [], BOUNDS).trace([emitter(waist=2 * MM)])
    reflected = result.segments[1].state
    assert reflected.q_t != pytest.approx(reflected.q_s)
    # Tangential focus is closer (f cos θ) than sagittal (f / cos θ).
    assert -reflected.q_t.real < -reflected.q_s.real


def test_cavity_is_bounded():
    left = mirror(0, 0, 0.0)
    right = mirror(10, 0, 180.0)
    tracer = BeamTracer([left, right], [], BOUNDS, max_segments=50)
    result = tracer.trace([emitter(x=5.0)])
    assert len(result.segments) <= 61


def test_nearest_and_path_to():
    result = BeamTracer([mirror(10, 0, 135.0)], [], BOUNDS).trace([emitter()])
    seg, s, dist = result.nearest((10.2, 4.0), 0.5)
    assert seg.index == 1
    assert s == pytest.approx(4.0)
    assert dist == pytest.approx(0.2)
    assert [x.index for x in result.path_to(seg)] == [0, 1]
    assert result.nearest((30.0, 30.0), 0.5) is None


def test_waist_downstream_of_source():
    e = Emitter((0.0, 0.0), 0.0, 1064 * NM, 100 * UM, waist_distance=3.0, owner="s")
    (seg,) = BeamTracer([], [], BOUNDS).trace([e]).segments
    assert seg.waist_positions() == [pytest.approx(3.0)]
    assert math.isclose(seg.state_at(3.0).tangential.radius, 100 * UM, rel_tol=1e-9)
