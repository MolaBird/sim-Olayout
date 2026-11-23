import json

import pytest

from olayout.core.breadboard import Breadboard
from olayout.core.layout import Component, IssueKey, Layout, LayoutFormatError, Measurement, Probe
from olayout.core.units import format_fraction, normalize_angle

from conftest import make_component


def sample_layout() -> Layout:
    layout = Layout(breadboard=Breadboard.from_preset("MB2436"), notes="bench A")
    make_component(layout, "laser", 1.0, 2.0, 0.0)
    m = make_component(layout, "km-mirror", 5.0, 2.0, 135.0)
    m.params["fork"] = {"angle": 90.0}
    make_component(layout, "collimator", 2.5, 10.5, 0.0)
    layout.measurements.append(Measurement(layout.new_id("m"), (0.5, 0.5), (3.5, 4.5)))
    layout.probes.append(Probe(layout.new_id("p"), 3.0, 2.0, "P1"))
    return layout


def test_round_trip_is_lossless(tmp_path):
    layout = sample_layout()
    path = tmp_path / "bench.olayout.json"
    layout.save(path)
    loaded = Layout.load(path)
    assert loaded.to_dict() == layout.to_dict()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == "olayout" and data["units"] == "inch"
    assert data["components"][1]["params"]["fork"]["angle"] == 90.0


def test_hand_written_minimal_file():
    layout = Layout.from_json('{"components": [{"type": "laser", "x": 1, "y": 2}]}')
    (comp,) = layout.components
    assert comp.id == "c1"
    assert (comp.x, comp.y, comp.angle) == (1.0, 2.0, 0.0)
    assert layout.breadboard == Breadboard()


@pytest.mark.parametrize(
    "text, message",
    [
        ("not json", "invalid JSON"),
        ('{"format": "other"}', "not an olayout file"),
        ('{"version": 99}', "unsupported layout version"),
        ('{"units": "mm"}', "inch"),
        ('{"components": [{"x": 1}]}', "no 'type'"),
        ('{"components": [{"type": "laser", "x": "left"}]}', "must be a number"),
    ],
)
def test_bad_files_raise_clear_errors(text, message):
    with pytest.raises(LayoutFormatError, match=message):
        Layout.from_json(text)


def test_duplicate_ids_are_repaired():
    layout = Layout.from_dict(
        {"components": [{"id": "c1", "type": "laser"}, {"id": "c1", "type": "laser"}, {"type": "laser"}]}
    )
    ids = [c.id for c in layout.components]
    assert len(set(ids)) == 3


def test_new_id_and_label():
    layout = Layout()
    layout.components = [Component("c3", "laser", label="M2"), Component("c10", "laser", label="M7")]
    assert layout.new_id() == "c11"
    assert layout.new_label("M") == "M8"
    assert layout.new_label("BS") == "BS1"


def test_angle_normalized_on_save():
    comp = Component("c1", "laser", angle=-90.0)
    assert comp.to_dict()["angle"] == 270.0
    assert normalize_angle(720.0) == 0.0


def test_fraction_formatting():
    assert format_fraction(1.5) == '1-1/2"'
    assert format_fraction(0.375) == '3/8"'
    assert format_fraction(2.0) == '2"'
    assert format_fraction(-0.25) == '-1/4"'
    assert format_fraction(0.3333).startswith("~")


def test_breadboard_holes():
    board = Breadboard.from_preset("MB1824")
    assert (board.columns, board.rows) == (24, 18)
    assert board.nearest_hole(3.2, 7.9) == (3.5, 7.5)
    assert board.hole_at(0.5, 0.5) == (1, 1)
    assert board.hole_at(0.6, 0.5) is None
    ring = board.holes_in_annulus(5.0, 5.0, 0.5, 1.0)
    assert len(ring) == 4  # the four diagonal neighbours at 0.707"


def test_ignored_problems_are_saved_and_follow_their_components():
    layout = sample_layout()
    laser, mirror = layout.components[0], layout.components[1]
    overlap = IssueKey.of("overlap", [mirror.id, laser.id], "optic")
    clipped = IssueKey.of("clipped", [laser.id])
    layout.ignored = {overlap, clipped}
    data = json.loads(layout.to_json())
    assert {"kind": "overlap", "components": sorted([laser.id, mirror.id]), "layer": "optic"} in data["ignored"]
    assert Layout.from_json(layout.to_json()).ignored == layout.ignored

    data["ignored"].append({"kind": "off-hole", "components": ["c999"]})  # no such component
    assert Layout.from_dict(data).ignored == layout.ignored

    layout.remove_components([mirror.id])  # a later component may reuse the id
    assert layout.ignored == {clipped}
    assert "ignored" not in Layout().to_dict()


@pytest.mark.parametrize("ignored", ["all", [{"kind": "overlap"}], [{"components": ["c1"]}], [3]])
def test_malformed_ignored_entries_are_rejected(ignored):
    with pytest.raises(LayoutFormatError):
        Layout.from_dict({"ignored": ignored})
