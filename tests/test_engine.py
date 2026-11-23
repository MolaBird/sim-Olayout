"""Placement checks, clamping forks and end-to-end beam behaviour of real units."""

import math

import pytest

from olayout.catalog.parts import PARTS, Layer
from olayout.catalog.units import DEFAULT_ROD_LENGTH, collimator_lens_range
from olayout.core.breadboard import Breadboard
from olayout.core.layout import IssueKey, Layout
from olayout.core.units import MM, NM, UM
from olayout.engine.analysis import Analyzer
from olayout.engine.validation import IssueKind
from olayout.optics.tracer import WarningKind

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
    fc.params["lens"] = {"focal_length_mm": 25.4, "distance_mm": 25.4}
    result = analyze(layout)
    fiber, out = result.trace.segments[:2]
    assert fiber.length == pytest.approx(25.4 * MM, rel=1e-9)
    expected = 780 * NM * 25.4 * MM / (math.pi * 2.5 * UM)
    assert out.state.tangential.radius == pytest.approx(expected, rel=0.01)
    # Fiber at the front focal plane -> output waist exactly one focal length after the lens.
    assert -out.state.tangential.distance_from_waist == pytest.approx(25.4 * MM, rel=1e-6)


def test_collimator_lens_distance_limited_by_cage_rods():
    """The CP35 slides on 1.5" rods that must stay in the SM1ZB plate."""
    near = collimator_lens_range("top")  # housing toward the fiber
    far = collimator_lens_range("top-flipped")  # housing toward the lens: CP35 must clear it
    assert near[1] < 35.0 * MM < far[1]
    assert far[0] > near[0]
    layout = Layout()
    fc = make_component(layout, "collimator", 2.5, 4.5)
    fc.params["lens"] = {"focal_length_mm": 35.0, "distance_mm": 35.0}
    result = analyze(layout)
    assert result.trace.segments[0].length == pytest.approx(near[1])
    assert any("clamped" in note for note in result.units[fc.id].build.notes)
    fc.orientation = "top-flipped"
    result = analyze(layout)
    assert result.trace.segments[0].length == pytest.approx(35.0 * MM)
    assert not any("clamped" in note for note in result.units[fc.id].build.notes)


def test_sm1zb_hangs_inverted_from_its_top_hole():
    """Its only 8-32 hole is in the top edge, so the knob ends up on top."""
    upright = PARTS["SM1ZB"]
    knob = next(p for p in upright.prims if getattr(p, "knurled", False))
    assert knob.center[2] < 0  # drawn with the housing and knob at the bottom
    for tap in ("top", "top-flipped"):
        oriented = upright.oriented(tap)
        knob = next(p for p in oriented.prims if getattr(p, "knurled", False))
        assert knob.center[2] > 0
        assert oriented.post_xy == pytest.approx((0.0, 0.0))  # post right under the optic axis
    # Flipping end-for-end moves the knob to the other side of the beam.
    a = next(p for p in upright.oriented("top").prims if getattr(p, "knurled", False))
    b = next(p for p in upright.oriented("top-flipped").prims if getattr(p, "knurled", False))
    assert a.center[1] == pytest.approx(-b.center[1]) and a.center[0] == pytest.approx(-b.center[0])


def test_pellicle_splits_on_its_front_face():
    layout = Layout()
    # The beam (+x) meets the membrane on the front of the BP108 frame at 20° incidence.
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
    assert transmitted.end_event == "escaped"  # out through the open side of the L-shaped plates


def _splitter_at_45(angle: float, orientation: str = "", layout: Layout | None = None):
    layout = Layout() if layout is None else layout
    bs = make_component(layout, "km-mirror", 0.0, 0.0, angle=angle)
    bs.params["optic"]["kind"] = "splitter"
    bs.orientation = orientation
    result = analyze(layout)
    cx, cy = optic_center(result, bs)
    bs.x += 8.0 - cx
    bs.y += 5.0 - cy
    make_component(layout, "laser", 2.0, 5.0)
    result = analyze(layout)
    transmitted = next(s for s in result.trace.segments if s.parent == 0 and s.direction[0] > 0.9)
    return bs, result, transmitted


def test_transmitted_beam_walking_into_the_l_plate_is_explained():
    bs, result, transmitted = _splitter_at_45(225.0)  # walks toward the side arm of the back plate
    assert transmitted.end_event == "blocked" and transmitted.end_owner == bs.id
    assert any("transmitted beam hits its own mount" in w.message for w in result.trace.warnings)
    _, result, transmitted = _splitter_at_45(225.0, "side")  # mounted on its side: the L is mirrored
    assert transmitted.end_event == "escaped"
    assert not any("own mount" in w.message for w in result.trace.warnings)
    _, _, transmitted = _splitter_at_45(135.0)  # walks toward the open side
    assert transmitted.end_event == "escaped"


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


def test_pellicle_unit_is_km100bp_with_bp108():
    layout = Layout()
    bs = make_component(layout, "mag-km-pellicle", 0.0, 0.0, angle=160.0)
    result = analyze(layout)
    assert {"KM100BP", "BP108", "KB1X1"} <= {p.part_id for p in result.units[bs.id].build.parts}
    cx, cy = optic_center(result, bs)
    bs.x += 8.0 - cx
    bs.y += 6.0 - cy
    make_component(layout, "laser", 2.0, 6.0)
    children = [s for s in analyze(layout).trace.segments if s.parent == 0]
    assert sorted(round(s.state.power, 6) for s in children) == [0.08, 0.92]  # BP108: 8:92 (R:T)


def test_cage_rod_length_sets_the_lens_range_and_is_drawn():
    short, long = collimator_lens_range("top"), collimator_lens_range("top", 4.0)
    assert long[0] == pytest.approx(short[0])
    assert long[1] - short[1] == pytest.approx(4.0 - DEFAULT_ROD_LENGTH)
    layout = Layout()
    fc = make_component(layout, "collimator", 2.5, 4.5)
    fc.params["rods"] = {"length_in": 4.0}
    fc.params["lens"] = {"focal_length_mm": 75.0, "distance_mm": 75.0}
    result = analyze(layout)
    build = result.units[fc.id].build
    rods = [p for p in build.parts if p.part_id == "ER"]
    assert len(rods) == 4
    assert all(r.footprint().bounds[2] - r.footprint().bounds[0] == pytest.approx(4.0, abs=0.01) for r in rods)
    assert result.trace.segments[0].length == pytest.approx(75.0 * MM)  # reachable with 4" rods
    assert build.elements and not build.optic_visuals  # the lens acts, but sits hidden in the CP35


def test_ignored_problems_stay_listed_but_are_not_illegal():
    layout = Layout()
    a = make_component(layout, "km-mirror", 6.0, 6.0)
    b = make_component(layout, "km-mirror", 6.3, 6.1)
    overlaps = [i for i in analyze(layout).issues if i.kind is IssueKind.OVERLAP]
    keys = {i.key for i in overlaps}
    assert {k.layer for k in keys} == {"base", "optic"}
    assert all(k.components == tuple(sorted((a.id, b.id))) for k in keys)

    layout.ignored = {IssueKey.of("overlap", [b.id, a.id], "optic")}  # id order does not matter
    result = analyze(layout)
    assert [i.layer for i in result.ignored_issues] == [Layer.OPTIC]
    assert Layer.BASE in {i.layer for i in result.active_issues}
    assert result.illegal_ids >= {a.id, b.id}  # the bases still collide

    layout.ignored = {i.key for i in analyze(layout).issues}
    result = analyze(layout)
    assert result.active_issues == [] and result.illegal_ids == set()
    assert len(result.ignored_issues) == len(result.issues) >= 2  # still found, just accepted
    assert result.issues_for(a.id) == []


def test_ignored_beam_warning_is_not_active():
    layout = Layout()
    bs, result, _ = _splitter_at_45(225.0, layout=layout)
    (warning,) = [w for w in result.trace.warnings if w.kind is WarningKind.OWN_MOUNT]
    assert warning.key == IssueKey("own-mount", (bs.id,))
    assert result.active_keys_for(bs.id) == [warning.key]
    layout.ignored = {warning.key}
    result = analyze(layout)
    assert result.ignored_warnings and not result.active_warnings
    assert result.active_keys_for(bs.id) == []
