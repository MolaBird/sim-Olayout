"""Placement checks, clamping forks and end-to-end beam behaviour of real units."""

import math

import pytest

from olayout.catalog import parts as P
from olayout.catalog.parts import PARTS, Layer
from olayout.catalog.units import COLLIMATOR_MIN_DISTANCE, DEFAULT_ROD_LENGTH, collimator_lens_range
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
    comp = make_component(layout, "mirror", 10.0, 9.0, angle=30.0)
    comp.params["clamp"] = {"angle": 90.0}  # on the table, whichever way the mirror faces
    fork = analyze(layout).units[comp.id].fork
    assert abs((fork.angle + comp.angle - 90.0 + 180) % 360 - 180) < 35


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
    assert near[0] == far[0] == COLLIMATOR_MIN_DISTANCE == pytest.approx(5.0 * MM)
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


def _expander_with_beam(type_id: str, w0_mm: float = 1.0):
    layout = Layout()
    bx = make_component(layout, type_id, 8.0, 10.0)
    make_component(layout, "laser", 2.0, 10.0, waist_um=w0_mm * 1000.0)
    return layout, bx, analyze(layout)


def _angle(seg) -> float:
    return math.degrees(math.atan2(seg.direction[1], seg.direction[0]))


@pytest.mark.parametrize("type_id, turn", [("beam-expander", 1.0), ("beam-expander-mirrored", -1.0)])
def test_beam_expander_folds_the_beam_and_expands_it_2x(type_id, turn):
    _, bx, result = _expander_with_beam(type_id)
    assert result.issues == [] and result.trace.warnings == []
    first, second, out = result.trace.segments
    assert first.end_event == second.end_event == "reflect" and first.end_owner == second.end_owner == bx.id
    assert first.end == pytest.approx((8.0, 10.0), abs=1e-6)  # mirror 1 sits at the component's position
    assert second.length == pytest.approx(24.64 * MM, abs=0.01 * MM)  # mirror 1 -> mirror 2 (STEP geometry)
    assert out.end_event == "escaped"  # out through the port in the 79° face
    assert _angle(out) == pytest.approx(turn * P.BE02R_EXIT_ANGLE, abs=1e-6)
    assert P.BE02R_EXIT_ANGLE == pytest.approx(9.57, abs=0.01)
    # Off-axis spheres: 2.0X in the fold plane, 1.9X across it.
    assert out.state.tangential.radius / MM == pytest.approx(2.03, abs=0.01)
    assert out.state.sagittal.radius / MM == pytest.approx(1.89, abs=0.01)


def test_beam_expander_works_backwards_as_a_reducer():
    layout = Layout()
    bx = make_component(layout, "beam-expander", 8.0, 10.0)
    # Send a Ø4 mm beam back along the output axis, from outside the output port.
    exit_mirror = P.BE02R_MIRRORS[1].center
    a = math.radians(P.BE02R_EXIT_ANGLE)
    start = (8.0 + exit_mirror[0] + 3.0 * math.cos(a), 10.0 + exit_mirror[1] + 3.0 * math.sin(a))
    make_component(layout, "laser", *start, angle=P.BE02R_EXIT_ANGLE + 180.0, waist_um=2000.0)
    first, second, out = analyze(layout).trace.segments
    assert first.end_owner == second.end_owner == bx.id
    assert out.start == pytest.approx((8.0, 10.0), abs=1e-6)  # back along the input axis
    assert out.direction == pytest.approx((-1.0, 0.0), abs=1e-6)
    assert out.state.tangential.radius / MM == pytest.approx(2.0 / 2.03, abs=0.02)
    assert out.end_event == "escaped"  # back out through the input port


def test_beam_expander_walls_block_stray_beams():
    layout = Layout()
    bx = make_component(layout, "beam-expander", 8.0, 10.0)
    make_component(layout, "laser", 8.0, 5.0, angle=90.0)  # straight into the side wall
    (seg,) = analyze(layout).trace.segments
    assert seg.end_event == "blocked" and seg.end_owner == bx.id
    assert seg.end[1] == pytest.approx(10.0 - 1.382, abs=0.005)  # the wall's outer face at x = 0


def test_mirrored_beam_expander_is_the_upside_down_part():
    upright = PARTS["BE02R"]
    _, bx, result = _expander_with_beam("beam-expander-mirrored")
    flipped = result.units[bx.id].build.parts[0]
    x0, y0, x1, y1 = upright.footprint().bounds
    assert flipped.footprint().bounds == pytest.approx((x0, -y1, x1, -y0), abs=1e-9)  # unit frame
    assert flipped.roll is not None and flipped.roll[2][2] == pytest.approx(-1.0)


@pytest.mark.parametrize("orientation", ["top", "top-flipped"])
def test_collimator_reaches_5_mm_with_the_lens_recessed(orientation):
    """Short-focus lenses sit closer than the CP35 can get, in a lens tube."""
    layout = Layout()
    fc = make_component(layout, "collimator", 2.5, 4.5)
    fc.orientation = orientation
    fc.params["lens"] = {"focal_length_mm": 5.95, "distance_mm": 5.95}
    result = analyze(layout)
    first = result.trace.segments[0]
    assert first.end_event == "transmit" and first.length == pytest.approx(5.95 * MM)
    build = result.units[fc.id].build
    assert any("recessed" in note for note in build.notes)
    assert result.issues == [] and result.trace.warnings == []
    # The CP35 rests against the SM1ZB carriage (or housing, when flipped): as close as it gets.
    cp35 = next(p for p in build.parts if p.part_id == "CP35")
    stop = P.SM1ZB_CARRIAGE_X if orientation == "top" else -P.SM1ZB_HOUSING_BACK
    assert cp35.pose.x - P.CP35_THICKNESS == pytest.approx(stop)  # its back face (the post is under the axis)
    fc.params["lens"]["distance_mm"] = 3.0
    result = analyze(layout)
    assert result.trace.segments[0].length == pytest.approx(5.0 * MM)  # the 5 mm minimum
    assert any("clamped" in note for note in result.units[fc.id].build.notes)


@pytest.mark.parametrize("type_id", ["kb-mirror", "kb-h-mirror"])
def test_fixed_mirror_on_a_kb1x1(type_id):
    layout = Layout()
    m = make_component(layout, type_id, 8.5, 5.5, angle=135.0)  # on a hole
    result = analyze(layout)
    parts = {p.part_id for p in result.units[m.id].build.parts}
    assert {"FMP1", "KB1X1", "PH2@BE1" if type_id == "kb-mirror" else "PH2"} <= parts
    _, cy = optic_center(result, m)
    make_component(layout, "laser", 2.0, cy)
    result = analyze(layout)
    assert result.issues == []
    first, second = result.trace.segments[:2]
    assert first.end_event == "reflect" and first.end_owner == m.id
    assert second.direction == pytest.approx((0.0, 1.0), abs=1e-9)  # folded by 90°


@pytest.mark.parametrize("type_id, event", [("kb-mirror", "reflect"), ("kb-h-mirror", "reflect"), ("mag-km-pellicle", "split")])
def test_mount_lifted_off_its_kb1x1_is_inert(type_id, event):
    layout = Layout()
    m = make_component(layout, type_id, 0.0, 0.0, angle=135.0 if event == "reflect" else 160.0)
    result = analyze(layout)
    cx, cy = optic_center(result, m)
    m.x, m.y = 8.0 - cx, 5.0 - cy  # optic on the beam (a hole-mounted unit then reports off-hole: fine here)
    make_component(layout, "laser", 2.0, 5.0)
    seated = analyze(layout)
    assert seated.trace.segments[0].end_event == event
    on_area = seated.units[m.id].footprints[Layer.OPTIC].area

    m.params.setdefault("kb1x1", {})["seated"] = False
    lifted = analyze(layout)
    build = lifted.units[m.id].build
    assert lifted.trace.segments[0].end_event == "escaped"  # the beam goes straight through
    assert build.elements == () and build.optic_visuals == ()
    assert {p.part_id for p in build.parts} >= {"KB1X1-BOTTOM"} and "KB1X1" not in {p.part_id for p in build.parts}
    mount = {"kb-mirror": "FMP1", "kb-h-mirror": "FMP1", "mag-km-pellicle": "KM100BP"}[type_id]
    assert mount in {p.part_id for p in build.detached_parts}  # still drawn, faintly
    assert lifted.units[m.id].footprints[Layer.OPTIC].area < on_area  # and out of the collision checks


@pytest.mark.parametrize("type_id", ["objective", "h-objective"])
def test_reflective_objective_focuses_at_its_working_distance(type_id):
    """The LMM40X acts as a thin lens (f = 5 mm) placed so that a collimated beam
    focuses one working distance in front of its nose."""
    layout = Layout()
    obj = make_component(layout, type_id, 10.5, 5.5)
    make_component(layout, "laser", 2.0, 5.5, waist_um=1000.0)
    result = analyze(layout)
    assert not result.issues and not result.trace.warnings  # the cage plate, adapter and housing let it through
    into, out = result.trace.segments[:2]
    assert into.end_event == "transmit" and into.end_owner == obj.id
    nose = obj.x + P.CP35_THICKNESS / 2 + P.LMM40X_NOSE
    (waist,) = out.waist_positions()
    # (The 1 mm input beam is not perfectly collimated: the focus moves by ~1e-5".)
    assert out.point_at(waist)[0] == pytest.approx(nose + P.LMM40X_WD_MM * MM, abs=1e-4)
    w_lens = into.state_at(into.length).tangential.radius
    expected = 633 * NM * P.LMM40X_EFL_MM * MM / (math.pi * w_lens)  # w0 = λ f / (π w)
    assert out.state_at(waist).tangential.radius == pytest.approx(expected, rel=1e-4)
    parts = {p.part_id for p in result.units[obj.id].build.parts}
    assert {"CP35", "M1RMSS", "LMM40X"} <= parts


def test_objective_clips_beams_wider_than_its_pupil():
    layout = Layout()
    make_component(layout, "objective", 10.5, 5.5)
    make_component(layout, "laser", 2.0, 5.5, waist_um=3000.0)  # w 3 mm on a Ø5.1 mm pupil
    result = analyze(layout)
    w = result.trace.segments[0].state_at(result.trace.segments[0].length).tangential.radius
    half_pupil = P.LMM40X_PUPIL_MM / 2 * MM
    assert result.trace.segments[0].clip == pytest.approx(math.erf(math.sqrt(2) * half_pupil / w), rel=1e-6)
    assert any(w.kind is WarningKind.CLIPPED for w in result.trace.warnings)


def test_square_mirror_in_a_kmsr_folds_the_beam():
    layout = Layout()
    m = make_component(layout, "sq-km-mirror", 0.0, 0.0, angle=135.0)
    result = analyze(layout)
    # A 6 mm PFSQ10 against the holder's back wall: the surface sits 0.427" + 6 mm in front of the post.
    surface = P.KMSR_HOLDER[0][1] + 6.0 * MM
    assert math.hypot(*optic_center(result, m)) == pytest.approx(surface, abs=1e-9)
    cx, cy = optic_center(result, m)
    m.x, m.y = 8.0 - cx, 5.0 - cy
    make_component(layout, "laser", 2.0, 5.0)
    result = analyze(layout)
    first, second = result.trace.segments[:2]
    assert first.end_event == "reflect" and first.end_owner == m.id
    assert second.direction == pytest.approx((0.0, 1.0), abs=1e-9)
    assert result.units[m.id].unit.orientations() == [("bottom", "Upright (bottom tap)")]


def test_kmsr_is_a_mirror_mount_with_a_solid_back():
    layout = Layout()
    m = make_component(layout, "sq-km-mirror", 0.0, 0.0, angle=180.0)
    m.params["optic"]["kind"] = "window"  # anything transmitted runs into the holder's back wall
    result = analyze(layout)
    cx, cy = optic_center(result, m)
    m.x, m.y = 8.0 - cx, 5.0 - cy
    make_component(layout, "laser", 2.0, 5.0)
    result = analyze(layout)
    assert result.trace.segments[1].end_event == "blocked" and result.trace.segments[1].end_owner == m.id


def _stage_at_focus(sample_kind: str):
    """A collimated beam, a reflective objective, and a MAX311D whose sample sits on the focus."""
    layout = Layout()
    make_component(layout, "laser", 2.0, 8.5, waist_um=1000.0)
    obj = make_component(layout, "objective", 9.0, 8.5)
    stage = make_component(layout, "sample-stage", 13.5, 9.5, angle=180.0)  # the sample faces -x
    stage.params["optic"]["kind"] = sample_kind
    focus = obj.x + P.CP35_THICKNESS / 2 + P.LMM40X_NOSE + P.LMM40X_WD_MM * MM
    sample_x, _ = optic_center(analyze(layout), stage)
    stage.params["sample"]["standoff_mm"] = (sample_x - focus) / MM  # the sample faces -x: forward is -x
    return layout, stage, focus


def test_sample_stage_holds_the_sample_at_the_objective_focus():
    layout, stage, focus = _stage_at_focus("block")
    result = analyze(layout)
    assert not result.issues
    assert optic_center(result, stage)[0] == pytest.approx(focus)
    into, onto = result.trace.segments[:2]
    assert onto.end_event == "blocked" and onto.end_owner == stage.id  # absorbed by the sample
    assert onto.state_at(onto.length).tangential.radius == pytest.approx(1.0 * UM, rel=0.02)  # in focus
    assert {p.part_id for p in result.units[stage.id].build.parts} == {"MAX311D", "SAMPLE"}


def test_a_reflective_sample_sends_the_beam_back_collimated():
    layout, stage, _ = _stage_at_focus("mirror")
    segments = analyze(layout).trace.segments
    back = [s for s in segments if s.direction[0] < -0.999]
    out = back[-1]  # back through the objective
    assert out.state.tangential.radius == pytest.approx(1.0 * MM, rel=0.01)
    # Collimated again: the same Rayleigh range (195") as the beam that came in.
    assert out.state.q_t.imag == pytest.approx(segments[0].state.q_t.imag, rel=1e-3)


def test_stage_travel_moves_the_sample_and_is_limited():
    layout, stage, _ = _stage_at_focus("block")
    x0, y0 = optic_center(analyze(layout), stage)
    stage.params["stage"] = {"focus_mm": 1.5, "lateral_mm": -0.5, "face": "x"}
    x1, y1 = optic_center(analyze(layout), stage)
    # The stage faces -x (angle 180): toward the objective is -x, "across" (+y in its frame) is -y.
    assert (x1 - x0, y1 - y0) == pytest.approx((-1.5 * MM, 0.5 * MM))
    stage.params["stage"]["focus_mm"] = 3.0
    result = analyze(layout)
    assert optic_center(result, stage)[0] - x0 == pytest.approx(-2.0 * MM)  # ±2 mm of travel
    assert any("clamped" in note for note in result.units[stage.id].build.notes)


def test_stage_sample_faces_are_mirror_images():
    layout = Layout()
    stage = make_component(layout, "sample-stage", 10.5, 10.5)
    left = optic_center(analyze(layout), stage)
    stage.params["stage"]["face"] = "y"
    right = optic_center(analyze(layout), stage)
    assert left[0] == pytest.approx(right[0]) and left[1] - 10.5 == pytest.approx(10.5 - right[1])
    assert left[0] - 10.5 == pytest.approx(55.2 * MM)  # the platform's face, 2.17" in front of the base centre


def test_stage_is_screwed_to_the_hole_grid():
    layout = Layout()
    stage = make_component(layout, "sample-stage", 10.3, 10.5)
    issues = analyze(layout).issues
    assert [i.kind for i in issues] == [IssueKind.OFF_HOLE] and "base centre" in issues[0].message
    stage.x = 10.5  # on a hole: its counterbores line up with the grid
    assert analyze(layout).issues == []


def _knife_edge_with_beams(offset: float, waist_um: float = 2200.0):
    """A knife edge at (10, 9) sending light toward -x, fed from above and below ``offset`` behind its edge."""
    layout = Layout()
    knife = make_component(layout, "knife-edge", 10.0, 9.0, angle=180.0)
    make_component(layout, "laser", 10.0 + offset, 12.0, angle=-90.0, waist_um=waist_um)
    make_component(layout, "laser", 10.0 + offset, 6.0, angle=90.0, waist_um=waist_um)
    return layout, knife


def test_knife_edge_combines_beams_from_opposite_sides():
    w = 2.2e3 * UM
    layout, knife = _knife_edge_with_beams(1.5 * w)
    result = analyze(layout)
    assert result.issues == []
    hits = [s for s in result.trace.segments if s.end_owner == knife.id]
    assert len(hits) == 2 and all(s.end_event == "reflect" for s in hits)
    # Each leg reaches right up to the edge: a beam 1.5 w from it keeps all but its far tail.
    radius = hits[0].state_at(hits[0].length).tangential.radius
    assert hits[0].clip == pytest.approx(0.5 * (1 + math.erf(math.sqrt(2) * 1.5 * w / radius)), rel=1e-6)
    out = [s for s in result.trace.segments if s.parent is not None]
    assert all(s.direction == pytest.approx((-1.0, 0.0), abs=1e-9) for s in out)
    assert sorted(s.start[1] for s in out) == pytest.approx([9.0 - 1.5 * w, 9.0 + 1.5 * w])
    assert not [x for x in result.trace.warnings if x.kind is WarningKind.CLIPPED]


def test_knife_edge_splits_a_beam_on_its_edge():
    layout, knife = _knife_edge_with_beams(1e-4)  # a hair inside each leg
    result = analyze(layout)
    hits = [s for s in result.trace.segments if s.end_owner == knife.id]
    assert len(hits) == 2 and all(s.clip == pytest.approx(0.5, abs=0.01) for s in hits)


def test_knife_edge_glass_stops_beams_from_behind():
    layout = Layout()
    knife = make_component(layout, "knife-edge", 10.0, 9.0, angle=180.0)
    make_component(layout, "laser", 14.0, 9.0, angle=180.0)  # into the hypotenuse
    (seg,) = analyze(layout).trace.segments
    assert seg.end_event == "blocked" and seg.end_owner == knife.id


@pytest.mark.parametrize("type_id", ["iris", "h-iris"])
def test_iris_passes_its_opening_and_stops_the_rest(type_id):
    layout = Layout()
    iris = make_component(layout, type_id, 10.5, 5.5, iris={"aperture_mm": 2.0})
    beam = make_component(layout, "laser", 2.0, 5.5, waist_um=1000.0)
    result = analyze(layout)
    first = result.trace.segments[0]
    assert first.end_event == "transmit" and first.end_owner == iris.id
    radius = first.state_at(first.length).tangential.radius
    assert first.clip == pytest.approx(math.erf(math.sqrt(2) * 1.0 * MM / radius), rel=1e-6)
    beam.y = 5.5 + 3.0 * MM  # beside the opening: the blades stop it
    (seg,) = analyze(layout).trace.segments
    assert seg.end_event == "blocked" and seg.end_owner == iris.id
    beam.y = 5.5 + 15.0 * MM  # beyond the housing's bore: the housing stops it
    (seg,) = analyze(layout).trace.segments
    assert seg.end_event == "blocked" and seg.end_owner == iris.id


PELLICLE_UNITS = {
    # unit id: (mount, ring)
    "mag-km-pellicle": ("KM100BP", "BP108"),
    "mag-km-h-pellicle": ("KM100BP", "BP108"),
    "mag-pellicle": ("BP107", "BP108"),
    "mag-h-pellicle": ("BP107", "BP108"),
    "mag-km-pellicle-2in": ("KM200BP", "BP208"),
    "mag-km-h-pellicle-2in": ("KM200BP", "BP208"),
    "mag-pellicle-2in": ("BP207", "BP208"),
    "mag-h-pellicle-2in": ("BP207", "BP208"),
}


@pytest.mark.parametrize("type_id", sorted(PELLICLE_UNITS))
def test_magnetic_pellicle_units(type_id):
    """Each pellicle mount on a KB1X1, on a pedestal or in a hole, splits 8:92 at
    the membrane on its ring's front face, and lifts off inert."""
    mount_id, ring_id = PELLICLE_UNITS[type_id]
    layout = Layout()
    bs = make_component(layout, type_id, 8.5, 5.5, angle=135.0)  # post on a hole
    result = analyze(layout)
    build = result.units[bs.id].build
    parts = {p.part_id: p for p in build.parts}
    hole = "-h-" in type_id
    assert {mount_id, ring_id, "KB1X1", "PH2" if hole else "PH2@BE1"} <= set(parts)
    assert ("BE1" in parts) is not hole
    # The ring sits on the mount's optic slot, the beam acting on its front face.
    element = build.elements[0]
    assert element.center == pytest.approx((parts[ring_id].pose.x, parts[ring_id].pose.y), abs=1e-4)  # (2 µm film)
    assert 2 * element.half_aperture == pytest.approx(P.PELLICLE_MOUNTS[mount_id].diameter)
    cx, cy = optic_center(result, bs)
    make_component(layout, "laser", 2.0, cy)
    result = analyze(layout)
    assert result.issues == [] and result.trace.warnings == []
    children = [s for s in result.trace.segments if s.parent == 0]
    assert sorted(round(s.state.power, 6) for s in children) == [0.08, 0.92]
    assert all(s.end_event == "escaped" for s in children)  # both beams leave clear of the mount

    bs.params["kb1x1"]["seated"] = False
    lifted = analyze(layout)
    assert lifted.trace.segments[0].end_event == "escaped"
    assert {mount_id, ring_id} <= {p.part_id for p in lifted.units[bs.id].build.detached_parts}


def test_pellicle_seats_measured_on_the_step_files():
    # The ring (3/16" thick) drops onto the KM100BP's lip, 0.03" below the front face.
    assert P.PELLICLE_FRAME_T == pytest.approx(0.1875)
    assert P.PELLICLE_PLANE_X == pytest.approx(0.5445)
    assert P.KM100BP_FRONT_X - P.PELLICLE_PLANE_X == pytest.approx(0.0275)
    # The BP107 cradle holds the membrane 0.95" above its tap, 0.165" in front of the post.
    tap = PARTS["BP107"].tap(None)
    assert tap.position == pytest.approx((-0.165, 0.0, -0.9492))
    assert PARTS["BP207"].tap(None).position[2] == pytest.approx(-1.4512)
    # The KM200BP: pellicle 1.605" from its bottom and +y faces, taps 1.56" from them.
    bottom, side = PARTS["KM200BP"].taps
    assert bottom.position[1:] == pytest.approx((0.045, -1.605))
    assert side.position[1:] == pytest.approx((1.605, -0.045))
