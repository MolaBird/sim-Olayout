"""The machined parts of layout v3, their shop drawings, and the v3 layout itself."""

import math
import re
import xml.etree.ElementTree as ET

import pytest

from olayout import paths
from olayout.catalog import parts as P
from olayout.catalog.units import THUMBSCREW_ANGLE, Mounting, mounting_of
from olayout.core.layout import Layout
from olayout.core.units import MM, UM
from olayout.engine.analysis import Analyzer
from olayout.tools import drawings

from conftest import make_component

V3_LAYOUT = paths.PROJECT_ROOT / "breadboard-optimized-v3.olayout.json"
DRAWINGS_DIR = paths.PROJECT_ROOT / "docs" / "drawings"


def analyze(layout):
    return Analyzer().run(layout)


# ---------------------------------------------------------------- units
def test_knife_edge_post_sends_beams_fed_along_its_column_9_mm_apart():
    layout = Layout()
    knife = make_component(layout, "knife-edge-post", 12.5, 9.5, angle=180.0)
    make_component(layout, "laser", 12.5, 13.0, angle=-90.0, waist_um=2200.0)  # down the post's hole column
    make_component(layout, "laser", 12.5, 6.0, angle=90.0, waist_um=2200.0)  # and up it
    result = analyze(layout)
    assert result.issues == []
    hits = [s for s in result.trace.segments if s.end_owner == knife.id]
    assert len(hits) == 2 and all(s.end_event == "reflect" for s in hits)
    assert all(s.clip > 0.9999 for s in hits)  # 4.5 mm (2 w) from the edge
    out = [s for s in result.trace.segments if s.parent is not None]
    assert all(s.direction == pytest.approx((-1.0, 0.0), abs=1e-9) for s in out)
    low, high = sorted(s.start[1] for s in out)
    assert (high - low) / MM == pytest.approx(2 * P.KNIFE_EDGE_LEAD / MM) == pytest.approx(9.0)
    assert (high + low) / 2 == pytest.approx(9.5)  # symmetric about the post


def test_beam_expander_riser_is_the_be02r_registered_on_two_holes():
    plain, risen = Layout(), Layout()
    a = make_component(plain, "beam-expander", 10.0, 15.5)
    b = make_component(risen, "beam-expander-riser", 10.0, 15.5)
    for layout in (plain, risen):
        make_component(layout, "laser", 4.0, 15.5)
    ra, rb = analyze(plain), analyze(risen)
    assert rb.issues == [] and rb.trace.warnings == []
    assert {p.part_id for p in rb.units[b.id].build.parts} == {"BE02R", "BE02R-RISER", "BE02R-RISER-WALL"}
    # Same optics, same output beam.
    ea, eb = ra.units[a.id].build.elements, rb.units[b.id].build.elements
    assert len(ea) == len(eb)
    for x, y in zip(ea, eb):
        assert y.center == pytest.approx(x.center) and y.normal_angle == pytest.approx(x.normal_angle)
    out_a, out_b = ra.trace.segments[-1], rb.trace.segments[-1]
    assert out_b.start == pytest.approx(out_a.start) and out_b.direction == pytest.approx(out_a.direction)
    # Mirror 1 on a hole row at a whole-inch x: both table bolts land on holes.
    for bx, by in P.RISER_BOLTS:
        assert risen.breadboard.hole_at(10.0 + bx, 15.5 + by) is not None


@pytest.mark.parametrize("type_id", ["beam-block", "beam-dump"])
def test_beam_blocks_and_dumps_stop_the_beam(type_id):
    layout = Layout()
    block = make_component(layout, type_id, 10.5, 5.5, angle=180.0)  # facing the beam
    make_component(layout, "laser", 2.0, 5.5)
    (seg,) = analyze(layout).trace.segments
    assert seg.end_event == "blocked" and seg.end_owner == block.id


@pytest.mark.parametrize("box", [P.BEAM_BLOCK, P.TARGET_PLATE], ids=["block", "target"])
def test_post_mounted_plates_are_thick_enough_for_their_8_32_tap(box):
    (x0, _, _), (x1, _, _) = box
    assert (x1 - x0 - drawings.TAP_8_32["major"]) / 2 >= 0.1  # wall left either side of the thread


def test_dual_pinhole_target_passes_beams_on_its_pinholes_only():
    layout = Layout()
    target = make_component(layout, "pinhole-target", 10.5, 5.5, target={"spacing_mm": 7.84, "hole_mm": 1.0})
    beam = make_component(layout, "laser", 2.0, 5.5 + 3.92 * MM, waist_um=300.0)
    first = analyze(layout).trace.segments[0]
    assert first.end_event == "transmit" and first.end_owner == target.id
    radius = first.state_at(first.length).tangential.radius
    assert first.clip == pytest.approx(math.erf(math.sqrt(2) * 0.5 * MM / radius), rel=1e-6)
    beam.y = 5.5  # between the pinholes: the plate stops it
    (seg,) = analyze(layout).trace.segments
    assert seg.end_event == "blocked" and seg.end_owner == target.id


def test_thumbscrew_side_turns_the_post_holder():
    layout = Layout()
    mirror = make_component(layout, "km-h-mirror", 10.5, 5.5)

    def ph2_turn():
        return next(p.pose.angle for p in analyze(layout).units[mirror.id].build.parts if p.part_id == "PH2")

    assert ph2_turn() == pytest.approx(THUMBSCREW_ANGLE)  # default: right, as before the option existed
    turns = set()
    for side in ("right", "left", "back", "front"):
        mirror.params.setdefault("post", {})["thumbscrew"] = side
        turns.add(round(ph2_turn() % 360.0, 6))
    assert len(turns) == 4


# ---------------------------------------------------------------- drawings
def test_drawings_are_well_formed_and_carry_the_model_dimensions(tmp_path):
    assert drawings.main(["--out", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.glob("*.svg")) == sorted(drawings.SHEETS)
    for path in tmp_path.glob("*.svg"):
        ET.parse(path)
    riser = (tmp_path / "be02r-riser.svg").read_text(encoding="utf-8")
    assert "1.9055" in riser  # bolt row to the wall face (datum A)
    holder = (tmp_path / "knife-edge-holder.svg").read_text(encoding="utf-8")
    assert f"{-P.KEH_FENCE_X:.4f} (13.18 mm)" in holder  # the fence face behind the post axis
    assert "7.84 mm" in (tmp_path / "dual-pinhole-target.svg").read_text(encoding="utf-8")


def test_committed_drawings_match_the_model(tmp_path):
    """docs/drawings must be regenerated (python -m olayout.tools.drawings) after changing a part."""
    drawings.main(["--out", str(tmp_path)])
    undated = re.compile(r"\d{4}-\d{2}-\d{2}")
    for name in drawings.SHEETS:
        fresh = undated.sub("DATE", (tmp_path / name).read_text(encoding="utf-8"))
        committed = undated.sub("DATE", (DRAWINGS_DIR / name).read_text(encoding="utf-8"))
        assert committed == fresh, f"docs/drawings/{name} is stale: run python -m olayout.tools.drawings"


# ---------------------------------------------------------------- the v3 layout
def _objective_spots(layout, result):
    """For each beam entering the objective: (source id, spot offset at the sample in µm, pupil crossing)."""
    obj = next(c for c in layout.components if c.type == "objective")
    pupil = obj.pose.apply(result.units[obj.id].build.elements[0].center)
    axis = (math.cos(math.radians(obj.angle)), math.sin(math.radians(obj.angle)))
    spots = []
    for s in result.trace.segments:
        along = axis[0] * s.direction[0] + axis[1] * s.direction[1]
        if s.end_owner != obj.id or along <= 0:
            continue
        t = ((pupil[0] - s.start[0]) * axis[0] + (pupil[1] - s.start[1]) * axis[1]) / along
        crossing = (s.start[0] + t * s.direction[0], s.start[1] + t * s.direction[1])
        tilt = math.atan2(axis[0] * s.direction[1] - axis[1] * s.direction[0], along)
        spots.append((s.source, P.LMM40X_EFL_MM * MM * math.tan(tilt) / UM, crossing))
    return pupil, spots


def test_layout_v3_is_clean_and_puts_the_spots_57_um_apart():
    layout = Layout.load(V3_LAYOUT)
    result = analyze(layout)
    labels = {c.id: c.label for c in layout.components}
    assert result.active_issues == []
    # What remains: the KM100BP pellicles' spare ports on their own mounts, and the pupil's 0.7% clip.
    assert {labels[w.owner] for w in result.active_warnings} == {"BS1", "BS2", "OBJ2"}
    pupil, spots = _objective_spots(layout, result)
    by_source = {labels[src]: (spot, crossing) for src, spot, crossing in spots}
    (top, top_cross), (bottom, bottom_cross) = by_source["FC3"], by_source["FC5"]
    assert top - bottom == pytest.approx(57.0, abs=0.1)
    assert top + bottom == pytest.approx(0.0, abs=0.1)
    for crossing in (top_cross, bottom_cross):  # both beams cross in the pupil's centre
        assert math.dist(crossing, pupil) / MM == pytest.approx(0.0, abs=0.01)
    # The HeNe tracers ride on the 2923 beams.
    assert by_source["FC4"][0] == pytest.approx(top, abs=0.01)
    assert by_source["FC6"][0] == pytest.approx(bottom, abs=0.01)


def test_layout_v3_mounts_every_mirror_in_a_hole():
    layout = Layout.load(V3_LAYOUT)
    board = layout.breadboard
    mirrors = [c for c in layout.components if c.label.startswith("M")]
    assert len(mirrors) == 16
    assert all(board.hole_at(c.x, c.y) is not None for c in mirrors)
    assert all(c.type in ("km100cp", "fmp1") and mounting_of(c) is Mounting.HOLE for c in mirrors)
