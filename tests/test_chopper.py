"""The MC2000B chopper head: its blade at the beam, and screwing it down through its own slots."""

import math

import pytest

from olayout.catalog import parts as P
from olayout.catalog.units import UNIT_TYPES, Mounting, mounting_of
from olayout.core.layout import Layout
from olayout.engine.analysis import Analyzer
from olayout.engine.slots import resolve_slots, slot_spots
from olayout.engine.validation import IssueKind

from conftest import make_component

CHOPPER = UNIT_TYPES["chopper"]


def beam_through(chopper_y_offset: float, height: float = 3.5):
    """A beam along +x crossing a chopper at (8.5, 7.0), ``chopper_y_offset`` to the side of its hub."""
    layout = Layout()
    source = make_component(layout, "laser", 2.0, 7.0 + chopper_y_offset, 0.0)
    chopper = make_component(layout, "chopper", 8.5, 7.0, 0.0)
    chopper.params["chopper"]["beam_height_in"] = height
    result = Analyzer().run(layout)
    first = next(s for s in result.trace.segments if s.source == source.id and s.parent is None)
    after = [s for s in result.trace.segments if s.parent == first.index]
    return layout, chopper, first, after, result


def test_it_screws_down_where_its_slots_catch_two_holes_3in_apart():
    layout, chopper, *_ = beam_through(0.9)
    assert mounting_of(chopper) is Mounting.SLOTS
    holes = resolve_slots(chopper, CHOPPER.slots, layout.breadboard)
    assert holes == ((8.5, 8.5), (8.5, 5.5))  # on its column, 1.5" either side
    for angle in (-30.0, 12.5, 30.0):  # the slots let it turn ±30° on the same two screws
        chopper.angle = angle
        assert resolve_slots(chopper, CHOPPER.slots, layout.breadboard) == holes
    chopper.angle = 35.0
    assert resolve_slots(chopper, CHOPPER.slots, layout.breadboard) == (None, None)
    issues = [i for i in Analyzer().run(layout).issues if chopper.id in i.component_ids]
    assert [i.kind for i in issues] == [IssueKind.NO_HOLE] and "3\" apart" in issues[0].message


def test_auto_mount_spots_are_where_both_slots_catch_holes():
    layout = Layout()
    chopper = make_component(layout, "chopper", 0.0, 0.0, 0.0)
    board = layout.breadboard
    spots = slot_spots(chopper, CHOPPER.slots, board, 8.3, 6.7, 1.0)
    assert spots[0] == (8.5, 7.0) and all(math.dist(s, (8.3, 6.7)) <= 1.0 for s in spots)
    for angle in (0.0, 20.0, 45.0, 90.0):  # at any angle, wherever it snaps it is screwed down
        chopper.angle = angle
        for x, y in slot_spots(chopper, CHOPPER.slots, board, 10.2, 9.6, 1.0):
            chopper.x, chopper.y = x, y
            assert None not in resolve_slots(chopper, CHOPPER.slots, board)
    chopper.angle, chopper.x, chopper.y = 0.0, 8.0, 6.5  # between two holes only 1" apart
    assert resolve_slots(chopper, CHOPPER.slots, board) == (None, None)
    drilled = board.with_drilled(8.0, 8.0).with_drilled(8.0, 5.0)  # holes drilled 1.5" either side
    assert resolve_slots(chopper, CHOPPER.slots, drilled) == ((8.0, 8.0), (8.0, 5.0))


def test_the_slotted_rings_chop_the_beam_and_the_hub_stops_it():
    *_, first, after, result = beam_through(0.9)  # 0.9" from the hub: the 10-slot ring
    assert first.end_event == "transmit" and after[0].state.power == pytest.approx(0.5)  # half the time
    *_, first, after, _ = beam_through(1.6)  # the 100-slot ring
    assert first.end_event == "transmit" and after[0].state.power == pytest.approx(0.5)
    *_, first, after, _ = beam_through(0.2)  # the solid hub
    assert first.end_event == "blocked" and not after
    *_, first, after, _ = beam_through(2.5)  # beside the blade: untouched
    assert first.end_event == "escaped"


def test_where_the_beam_crosses_the_blade_follows_the_beam_height():
    *_, first, after, _ = beam_through(0.2, height=P.MC2000B_HUB[2] + 1.0)  # 1" above the hub: inner slots
    assert first.end_event == "transmit" and after[0].state.power == pytest.approx(0.5)
    *_, first, _, _ = beam_through(0.2, height=5.5)  # above the blade and the body
    assert first.end_event == "escaped"


def test_its_base_takes_room_on_the_table():
    layout = Layout()
    make_component(layout, "chopper", 8.5, 7.0, 0.0)
    post = make_component(layout, "post", 8.5, 8.5)  # a post holder on the base plate
    issues = [i for i in Analyzer().run(layout).issues if post.id in i.component_ids]
    assert any(i.kind is IssueKind.OVERLAP and i.key.layer == "base" for i in issues)
