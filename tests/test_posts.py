"""Posts as building blocks: holders, clamps, KB1X1s, mounts on top, drilled holes,
and files from before posts were split from their mounts."""

import math

import pytest

from olayout.catalog import parts as P
from olayout.catalog.units import (
    BARE_POST,
    Mounting,
    catalog_parts,
    component_bom,
    has_mount,
    library_entry,
    lifted_off,
    mount_on_post_params,
    mounting_of,
    upgrade_component,
)
from olayout.core.breadboard import Breadboard
from olayout.core.layout import Component, Layout
from olayout.engine.analysis import Analyzer
from olayout.engine.fork import hole_toward, reachable_holes, resolve_fork
from olayout.engine.validation import IssueKind
from olayout.engine.world import BuildCache

from conftest import on_post


def part_ids(comp: Component) -> list[str]:
    return [p.part_id for p in BuildCache().build(comp).parts]


def issue_kinds(layout: Layout, comp: Component) -> set:
    return {i.kind for i in Analyzer().run(layout).issues if comp.id in i.component_ids}


# ------------------------------------------------------------------ holders
def test_a_bare_post_stands_in_a_hole_or_on_a_clamped_pedestal():
    hole = on_post(BARE_POST)
    clamped = on_post(BARE_POST, holder="clamped")
    assert part_ids(hole) == ["PH2"] and mounting_of(hole) is Mounting.HOLE
    assert part_ids(clamped) == ["BE1", "PH2@BE1"] and mounting_of(clamped) is Mounting.PEDESTAL
    assert not has_mount(hole) and component_bom(clamped) == ("PH2", "BE1", "CF125C", 'TR-series Ø1/2" post')


@pytest.mark.parametrize("type_id", ["km100", "fmp1", "pellicle-bp107", "collimator", "objective", "iris", "beam-dump"])
def test_any_mount_can_stand_on_either_holder(type_id):
    for holder, base in (("hole", ["PH2"]), ("clamped", ["BE1", "PH2@BE1"])):
        comp = on_post(type_id, holder=holder)
        assert part_ids(comp)[: len(base)] == base and has_mount(comp)
        build = BuildCache().build(comp)
        assert build.elements or build.emitters or build.blockers


def test_switching_the_holder_changes_how_the_post_must_be_placed():
    layout = Layout()
    comp = on_post("km100", "m", "M1", x=10.2, y=9.3)  # between holes
    layout.components.append(comp)
    assert IssueKind.OFF_HOLE in issue_kinds(layout, comp)
    comp.params["base"]["kind"] = "clamped"
    assert not issue_kinds(layout, comp)  # the clamp finds a hole by itself


# ------------------------------------------------------------- mounts, KB1X1
def test_a_mount_put_on_a_post_keeps_the_posts_holder_clamp_and_kb1x1():
    post = on_post(BARE_POST, holder="clamped", kb1x1=True)
    post.params["base"]["clamp"] = "CF038"
    post.params["clamp"]["angle"] = 45.0
    params = mount_on_post_params(post, "km100", {"optic": {"kind": "splitter"}, "base": {"kind": "hole"}})
    assert params["base"] == {"kind": "clamped", "clamp": "CF038"} and params["clamp"]["angle"] == 45.0
    assert params["kb1x1"] == {"present": True, "seated": True} and params["optic"]["kind"] == "splitter"


@pytest.mark.parametrize("type_id", [BARE_POST, "km100", "collimator", "objective", "pellicle-km200bp"])
def test_a_kb1x1_sits_right_under_whatever_is_on_the_post(type_id):
    comp = on_post(type_id, kb1x1=True)
    build = BuildCache().build(comp)
    (kb1x1,) = [p for p in build.parts if p.part_id == "KB1X1"]
    tops = [p for p in build.parts if p.part_id not in ("PH2", "KB1X1")]
    assert all(p.z_offset >= kb1x1.z_offset - 1e-9 for p in tops)
    assert "KB1X1" in component_bom(comp)


def test_any_mount_on_a_kb1x1_lifts_off_and_goes_back():
    comp = on_post("collimator", kb1x1=True)
    assert BuildCache().build(comp).emitters
    comp.params["kb1x1"]["seated"] = False
    build = BuildCache().build(comp)
    assert lifted_off(comp) and not build.emitters and not build.elements
    assert [p.part_id for p in build.parts] == ["PH2", "KB1X1-BOTTOM"]
    assert {"SM1ZB", "CP35"} <= {p.part_id for p in build.detached_parts}
    # Without a KB1X1 there is nothing to lift off: the flag does nothing.
    comp.params["kb1x1"]["present"] = False
    assert not lifted_off(comp) and BuildCache().build(comp).emitters


# ------------------------------------------------------------------- clamps
def test_the_short_clamp_reaches_only_the_nearer_holes():
    board = Breadboard()
    post = on_post(BARE_POST, holder="clamped", x=10.5, y=9.0)  # on a hole column, between two rows
    long_reach = reachable_holes(post, board)
    post.params["base"]["clamp"] = "CF038"
    short_reach = reachable_holes(post, board)
    assert 0 < len(short_reach) < len(long_reach)
    lo, hi = P.CLAMPS["CF038"].screw_travel
    assert all(lo <= math.dist(h, (10.5, 9.0)) <= hi for h in short_reach)
    fork = resolve_fork(post, board, [])
    assert fork.clamp == "CF038" and fork.hole is not None
    short_outline = P.get_part("CF038").footprint()
    assert short_outline.bounds[2] < P.get_part("CF125C").footprint().bounds[2] - 0.5


def test_a_clamp_direction_is_set_on_the_table_and_stays_as_the_mount_turns():
    board = Breadboard()
    comp = on_post("km100", holder="clamped", x=10.0, y=9.0)
    comp.params["clamp"]["angle"] = 90.0
    directions = set()
    for angle in (0.0, 30.0, 135.0, 250.0):
        comp.angle = angle
        fork = resolve_fork(comp, board, [])
        directions.add(round((fork.angle + angle) % 360.0, 6))
        assert fork.hole == hole_toward(comp, board, 90.0)
    assert len(directions) == 1


def test_an_old_relative_fork_preference_becomes_a_table_direction():
    old = Component("a", "km-mirror", 10.0, 9.0, angle=90.0, label="M1", params={"fork": {"angle": 45.0}})
    new = upgrade_component(old)
    assert new.type == "km100" and new.params["base"]["kind"] == "clamped"
    assert new.params["clamp"]["angle"] == pytest.approx(135.0) and "fork" not in new.params


# ------------------------------------------------------------- drilled holes
def test_holes_can_only_be_drilled_at_free_cell_centres():
    board = Breadboard()
    assert board.drill_problem(5.0, 5.0) is None
    assert "centre of a grid cell" in board.drill_problem(5.5, 5.5)  # a grid hole
    assert "centre of a grid cell" in board.drill_problem(5.2, 5.0)
    assert "counterbored" in board.drill_problem(1.0, 1.0)  # the board's own mounting hole
    drilled = board.with_drilled(5.0, 5.0)
    assert drilled.drilled == ((5.0, 5.0),) and "already" in drilled.drill_problem(5.0, 5.0)
    assert board.with_drilled(5.2, 5.0) is board  # refused quietly
    assert drilled.hole_at(5.0, 5.0) == (5.5, 5.5)  # between holes 5 and 6 each way
    assert drilled.nearest_hole(5.1, 4.9) == (5.0, 5.0)
    assert Breadboard.from_dict(drilled.to_dict()) == drilled
    assert drilled.without_drilled(5.0, 5.0) == board


def test_a_post_screws_into_a_drilled_hole_and_a_clamp_reaches_one():
    board = Breadboard()
    layout = Layout(breadboard=board)
    post = on_post("fmp1", "p", "M1", x=5.0, y=5.0)
    layout.components.append(post)
    assert IssueKind.OFF_HOLE in issue_kinds(layout, post)
    layout.breadboard = board.with_drilled(5.0, 5.0)
    assert not issue_kinds(layout, post)

    # A short clamp at a cell centre reaches no grid hole (they are 0.71" or 1.58" away)...
    clamped = on_post(BARE_POST, "c", "PH1", holder="clamped", x=9.0, y=9.0)
    clamped.params["base"]["clamp"] = "CF038"
    layout.components[:] = [clamped]
    assert IssueKind.NO_HOLE in issue_kinds(layout, clamped)
    # ...but a hole drilled at the diagonal cell centre (1.41" away) is in reach.
    layout.breadboard = layout.breadboard.with_drilled(10.0, 10.0)
    assert not issue_kinds(layout, clamped)
    assert Analyzer().run(layout).units["c"].fork.hole == (10.0, 10.0)


# --------------------------------------------------------- older files
def test_files_from_before_posts_are_upgraded_on_load():
    data = {
        "format": "olayout",
        "version": 1,
        "units": "inch",
        "breadboard": {"width": 21, "height": 20},
        "components": [
            {"id": "a", "type": "kb-h-mirror", "x": 5.5, "y": 5.5, "label": "M1", "params": {"kb1x1": {"seated": False}}},
            {"id": "b", "type": "base", "x": 3.0, "y": 3.0, "label": "B1", "params": {"fork": {"angle": None}}},
            {"id": "c", "type": "lens", "x": 7.0, "y": 7.0, "label": "L1", "params": {"optic": {"kind": "lens"}}},
            {"id": "d", "type": "beam-dump", "x": 8.5, "y": 8.5, "label": "BB1"},
            {"id": "e", "type": "mag-km-pellicle", "x": 9.0, "y": 9.0, "label": "BS1"},
        ],
    }
    layout = Layout.from_dict(data)
    by_id = {c.id: c for c in layout.components}
    assert by_id["a"].type == "fmp1" and mounting_of(by_id["a"]) is Mounting.HOLE and lifted_off(by_id["a"])
    assert by_id["b"].type == BARE_POST and mounting_of(by_id["b"]) is Mounting.PEDESTAL
    assert by_id["c"].type == "lens" and mounting_of(by_id["c"]) is Mounting.PEDESTAL  # lenses were clamped
    assert mounting_of(by_id["d"]) is Mounting.HOLE  # dumps were in holes
    assert by_id["e"].type == "pellicle-km100bp" and by_id["e"].params["kb1x1"]["present"]
    again = Layout.from_dict(layout.to_dict())
    assert again.to_dict() == layout.to_dict()  # a saved file is current: loading it changes nothing
    assert all(Analyzer().run(again).units[c.id].build.parts for c in again.components)


def test_old_type_ids_still_name_library_entries():
    entry = library_entry("kb-mirror")
    assert entry.type_id == "fmp1"
    assert entry.initial_params()["base"]["kind"] == "clamped" and entry.initial_params()["kb1x1"]["present"]


def test_every_clamp_and_kb1x1_variant_is_in_the_catalog():
    ids = {p.part_id for p in catalog_parts()}
    assert {"CF125C", "CF038", "KB1X1", "KB1X1-BOTTOM", "PH2", "PH2@BE1", "BE1"} <= ids
