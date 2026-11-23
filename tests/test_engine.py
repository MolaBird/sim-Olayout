"""Placement checks, clamping forks and end-to-end beam behaviour of real units."""

import math

import pytest

from olayout.catalog.parts import Layer
from olayout.core.breadboard import Breadboard
from olayout.core.layout import Layout
from olayout.core.units import MM, NM, UM
from olayout.engine.analysis import Analyzer
from olayout.engine.validation import IssueKind

from conftest import make_component


def analyze(layout):
    return Analyzer().run(layout)


def kinds(result):
    return sorted(i.kind for i in result.issues)


def optic_center(result, comp):
    element = result.units[comp.id].build.elements[0]
    return tuple(comp.pose.apply(element.center))


def test_separated_units_are_legal():
    layout = Layout()
    make_component(layout, "km-mirror", 5.0, 5.0)
    make_component(layout, "km-mirror", 12.0, 5.0)
    make_component(layout, "h-mirror", 8.5, 12.5)
    assert analyze(layout).issues == []


def test_overlapping_units_flagged_in_both_layers():
    layout = Layout()
    a = make_component(layout, "km-mirror", 6.0, 6.0)
    b = make_component(layout, "km-mirror", 6.3, 6.1)
    result = analyze(layout)
    overlaps = [i for i in result.issues if i.kind is IssueKind.OVERLAP]
    assert {i.layer for i in overlaps} == {Layer.BASE, Layer.OPTIC}
    assert all(set(i.component_ids) == {a.id, b.id} for i in overlaps)
    assert all(i.region.area > 0 for i in overlaps)


def test_layers_do_not_collide_with_each_other():
    """A mount may overhang another unit's base: they sit at different heights."""
    layout = Layout()
    a = make_component(layout, "km-mirror", 6.0, 6.0, angle=0.0)
    result = analyze(layout)
    optic = result.units[a.id].footprints[Layer.OPTIC]
    # Place a hole-mounted unit whose base lies under a's mount but whose own mount is clear.
    xmin, ymin, xmax, ymax = optic.bounds
    b = make_component(layout, "base", 6.0, 6.0 + 2.6)
    result = analyze(layout)
    base_b = result.units[b.id].footprints[Layer.BASE]
    assert not base_b.intersects(result.units[a.id].footprints[Layer.BASE]) or any(
        i.layer is Layer.BASE for i in result.issues
    )
    assert all(i.layer is not Layer.OPTIC for i in result.issues)


def test_hole_mounted_unit_must_sit_on_a_hole():
    layout = Layout()
    make_component(layout, "h-mirror", 5.0, 5.0)
    assert kinds(analyze(layout)) == [IssueKind.OFF_HOLE]
    layout.components[0].x, layout.components[0].y = 5.5, 5.5
    assert analyze(layout).issues == []


def test_pedestal_fork_reaches_a_hole():
    layout = Layout()
    comp = make_component(layout, "km-mirror", 7.3, 6.8, angle=30.0)
    result = analyze(layout)
    fork = result.units[comp.id].fork
    assert fork is not None and fork.hole is not None
    board = layout.breadboard
    assert board.hole_at(*fork.hole) is not None
    assert fork.screw_distance == pytest.approx(math.dist((comp.x, comp.y), fork.hole))
    # The fork points from the pedestal toward the chosen hole.
    world = math.radians(fork.angle + comp.angle)
    tip = (comp.x + fork.screw_distance * math.cos(world), comp.y + fork.screw_distance * math.sin(world))
    assert tip == pytest.approx(fork.hole, abs=1e-6)


def test_manual_fork_direction_is_respected():
    layout = Layout()
    comp = make_component(layout, "mirror", 10.0, 9.0)
    comp.params["fork"] = {"angle": 90.0}
    fork = analyze(layout).units[comp.id].fork
    assert abs((fork.angle - 90.0 + 180) % 360 - 180) < 35


def test_pedestal_off_board_has_no_hole():
    layout = Layout(breadboard=Breadboard(width=12, height=12, name="small"))
    make_component(layout, "km-mirror", 30.0, 30.0)
    assert IssueKind.NO_HOLE in kinds(analyze(layout))


def test_base_overhanging_edge_is_flagged():
    layout = Layout()
    make_component(layout, "base", 0.1, 9.0)
    assert IssueKind.OFF_BOARD in kinds(analyze(layout))


def test_unknown_type_does_not_crash():
    layout = Layout.from_dict({"components": [{"type": "warp-drive", "x": 1, "y": 1}, {"type": "laser"}]})
    result = analyze(layout)
    assert kinds(result) == [IssueKind.INVALID]


def test_mirror_unit_folds_beam_end_to_end():
    layout = Layout()
    mirror = make_component(layout, "km-mirror", 0.0, 0.0, angle=135.0)
    result = analyze(layout)
    cx, cy = optic_center(result, mirror)
    # Re-position so the optic centre sits on y = 3, x = 8, then aim a source at it.
    mirror.x += 8.0 - cx
    mirror.y += 3.0 - cy
    make_component(layout, "laser", 1.0, 3.0, angle=0.0)
    result = analyze(layout)
    first, second = result.trace.segments[:2]
    assert first.end_event == "reflect" and first.end_owner == mirror.id
    assert second.direction == pytest.approx((0.0, 1.0), abs=1e-9)


def test_beam_missing_the_optic_hits_the_mount():
    layout = Layout()
    mirror = make_component(layout, "km-mirror", 8.0, 3.0, angle=135.0)  # optic is offset from the post
    make_component(layout, "laser", 1.0, 3.0, angle=0.0)
    result = analyze(layout)
    assert result.trace.segments[0].end_event == "blocked"
    assert result.trace.segments[0].end_owner == mirror.id


def test_collimator_output_is_collimated():
    layout = Layout()
    fc = make_component(layout, "collimator", 2.5, 4.5)
    fc.params["source"]["wavelength_nm"] = 780.0
    fc.params["source"]["mfd_um"] = 5.0
    fc.params["lens"] = {"focal_length_mm": 35.0, "distance_mm": 35.0}
    result = analyze(layout)
    fiber, out = result.trace.segments[:2]
    assert fiber.length == pytest.approx(35.0 * MM, rel=1e-9)
    expected = 780 * NM * 35.0 * MM / (math.pi * 2.5 * UM)
    assert out.state.tangential.radius == pytest.approx(expected, rel=0.01)
    # Fiber at the front focal plane -> output waist exactly one focal length after the lens.
    assert -out.state.tangential.distance_from_waist == pytest.approx(35.0 * MM, rel=1e-6)


def test_pellicle_splits_and_transmits_through_mount():
    layout = Layout()
    # 20° incidence: the transmitted beam walks only ~0.3" sideways through the mount.
    bs = make_component(layout, "mag-km-pellicle", 0.0, 0.0, angle=160.0)
    bs.params["optic"]["reflectance"] = 0.4
    result = analyze(layout)
    cx, cy = optic_center(result, bs)
    bs.x += 8.0 - cx
    bs.y += 6.0 - cy
    make_component(layout, "laser", 2.0, 6.0)
    result = analyze(layout)
    children = [s for s in result.trace.segments if s.parent == 0]
    assert sorted(round(s.state.power, 6) for s in children) == [0.4, 0.6]
    transmitted = next(s for s in children if s.direction[0] > 0.9)
    assert transmitted.end_event == "escaped"  # passed through the back plate's clearance hole


def test_orientations_change_top_view_and_post_offset():
    layout = Layout()
    km = make_component(layout, "km-mirror", 5.0, 5.0)
    cp = make_component(layout, "km-h-mirror", 10.5, 5.5)

    def state():
        units = analyze(layout).units
        mount = lambda c: next(p for p in units[c.id].build.parts if p.part_id.startswith("KM"))
        return mount(km).prims, units[cp.id].build.elements[0].center

    km_upright, cp_upright = state()
    km.orientation = cp.orientation = "side"
    km_side, cp_side = state()
    assert km_upright != km_side  # adjusters seen from another side
    # KM100CP: both taps sit directly under the mirror's front surface.
    assert cp_upright == pytest.approx((0.0, 0.0)) and cp_side == pytest.approx((0.0, 0.0))


def test_mirror_surface_follows_optic_thickness():
    """KM100 is front-loaded onto a lip 0.335" in front of its post."""
    layout = Layout()
    km = make_component(layout, "km-mirror", 5.0, 5.0)
    assert analyze(layout).units[km.id].build.elements[0].center[0] == pytest.approx(0.335 + 6.0 * MM)
    km.params["optic"]["thickness_mm"] = 9.5
    assert analyze(layout).units[km.id].build.elements[0].center[0] == pytest.approx(0.335 + 9.5 * MM)


def test_incremental_forks_match_full_resolve():
    """Re-solving forks only near changes must equal re-solving all of them."""
    layout = Layout()
    comps = [make_component(layout, "km-mirror", 3.0 + 2.2 * i, 6.0 + (i % 2) * 1.3, 30.0 * i) for i in range(6)]
    fast, full = Analyzer(incremental=True), Analyzer(incremental=False)
    for step in range(15):
        comps[2].x += 0.15  # drag one pedestal through its neighbours
        comps[2].angle += 7.0
        a, b = fast.run(layout), full.run(layout)
        for comp in comps:
            assert a.units[comp.id].fork == b.units[comp.id].fork, f"step {step}, {comp.label}"
        assert [i.message for i in a.issues] == [i.message for i in b.issues]


def test_build_cache_survives_moves():
    analyzer = Analyzer()
    layout = Layout()
    comp = make_component(layout, "km-mirror", 5.0, 5.0)
    first = analyzer.run(layout).units[comp.id].build
    comp.x += 0.37
    second = analyzer.run(layout).units[comp.id].build
    assert first is second
