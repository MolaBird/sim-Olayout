"""Enclosure framing: 1/2" T-slot rails and posts, and boards."""

import pytest

from olayout.catalog.parts import Layer
from olayout.core.layout import Layout
from olayout.engine.analysis import Analyzer
from olayout.engine.validation import IssueKind

from conftest import make_component


def analyze(layout):
    return Analyzer().run(layout)


def _beam_across(layout, member_type, **params):
    """A beam along +x at y = 5 and a member crossing it at x = 8."""
    make_component(layout, "laser", 2.0, 5.0)
    member = make_component(layout, member_type, 8.0, 2.0, 90.0)  # runs from (8, 2) toward +y
    for key, value in params.items():
        member.params[key.split(".")[0]][key.split(".")[1]] = value
    return member


@pytest.mark.parametrize("member_type", ["tslot-rail", "board"])
def test_rails_at_beam_height_and_boards_block_beams(member_type):
    layout = Layout()
    member = _beam_across(layout, member_type)
    first = analyze(layout).trace.segments[0]
    assert first.end_event == "blocked" and first.end_owner == member.id
    half = 0.25 if member_type == "tslot-rail" else 0.0625
    assert first.end[0] == pytest.approx(8.0 - half)  # stopped on its near face


def test_a_rail_lying_on_the_table_lets_beams_pass_over():
    layout = Layout()
    _beam_across(layout, "tslot-rail", **{"rail.run": "table"})
    result = analyze(layout)
    assert result.trace.segments[0].end_event == "escaped"
    rail = next(u for u in result.units.values() if u.unit.id == "tslot-rail")
    assert rail.footprints[Layer.OPTIC].is_empty and rail.footprints[Layer.BASE].area == pytest.approx(6.0)


def test_a_post_blocks_beams_and_takes_space_in_both_layers():
    layout = Layout()
    make_component(layout, "laser", 2.0, 5.0)
    post = make_component(layout, "tslot-post", 8.0, 5.0)
    result = analyze(layout)
    assert result.trace.segments[0].end_event == "blocked" and result.trace.segments[0].end_owner == post.id
    placed = result.units[post.id]
    assert placed.footprints[Layer.OPTIC].area > 0.1
    assert placed.footprints[Layer.BASE].area == pytest.approx(placed.footprints[Layer.OPTIC].area)


def test_length_runs_along_the_angle_and_is_clamped():
    layout = Layout()
    rail = make_component(layout, "tslot-rail", 3.0, 4.0, 90.0)
    rail.params["rail"]["length_in"] = 7.5
    minx, miny, maxx, maxy = analyze(layout).units[rail.id].footprints[Layer.OPTIC].bounds
    assert (minx, miny, maxx, maxy) == pytest.approx((2.75, 4.0, 3.25, 11.5))
    rail.params["rail"]["length_in"] = -3.0  # hand-edited nonsense: clamped to the shortest
    assert analyze(layout).units[rail.id].footprints[Layer.OPTIC].bounds[3] == pytest.approx(4.5)


def test_framing_joins_without_collisions_but_collides_with_the_rest():
    layout = Layout()
    # An enclosure corner: two rails meeting, a post on the corner, a board in a rail's slot.
    make_component(layout, "tslot-rail", 2.0, 2.0, 0.0)
    make_component(layout, "tslot-rail", 2.0, 2.0, 90.0)
    make_component(layout, "tslot-post", 2.0, 2.0)
    make_component(layout, "board", 2.0, 2.0, 0.0)
    assert analyze(layout).issues == []

    mount = make_component(layout, "km-mirror", 6.0, 2.6, 90.0)  # its mount sits on the rail / board line
    result = analyze(layout)
    overlaps = [i for i in result.issues if i.kind is IssueKind.OVERLAP and mount.id in i.component_ids]
    assert {i.layer for i in overlaps} == {Layer.OPTIC, Layer.BASE}  # the board stands on the table, too


def test_a_rail_on_the_table_only_meets_bases():
    layout = Layout()
    rail = make_component(layout, "tslot-rail", 2.0, 6.0, 0.0)
    rail.params["rail"]["run"] = "table"
    mirror = make_component(layout, "h-mirror", 6.5, 6.5, 0.0)  # its PH2 (Ø0.98") straddles the rail
    issues = [i for i in analyze(layout).issues if i.kind is IssueKind.OVERLAP]
    assert [(set(i.component_ids), i.layer) for i in issues] == [({rail.id, mirror.id}, Layer.BASE)]


def test_framing_is_free_standing_even_off_the_board():
    layout = Layout()
    make_component(layout, "board", -1.0, -1.0, 0.0)  # around the breadboard, on the optical table
    assert analyze(layout).issues == []
