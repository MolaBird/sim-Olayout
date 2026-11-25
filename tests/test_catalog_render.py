"""Every part and unit builds, renders and exposes sane geometry."""

import numpy as np
import pytest
from shapely.geometry import Point

from olayout.catalog.parts import PARTS, Layer
from olayout.catalog.primitives import Box, Cylinder, rotation_matrix, transform
from olayout.catalog.units import UNIT_TYPES, Mounting, default_fork, new_component_params
from olayout.core.layout import Component
from olayout.engine.world import BuildCache
from olayout.render.sprites import SpriteCache, render_sprite


@pytest.mark.parametrize("part_id", sorted(PARTS))
def test_part_footprint_nonempty(part_id):
    part = PARTS[part_id]
    assert part.footprint().area > 0
    for tap in part.taps:
        oriented = part.oriented(tap.name)
        assert oriented.prims


@pytest.mark.parametrize("type_id", sorted(UNIT_TYPES))
def test_unit_builds_with_defaults(type_id):
    unit = UNIT_TYPES[type_id]
    for orientation in [""] + [name for name, _ in unit.orientations()]:
        comp = Component("c1", type_id, orientation=orientation, params=new_component_params(type_id))
        fork = default_fork() if unit.mounting is Mounting.PEDESTAL else None
        placed = BuildCache().place(comp, fork)
        if placed.is_virtual:  # a beam source: no mechanical parts
            assert unit.mounting is Mounting.FREE and placed.build.emitters
            continue
        if unit.mounting is not Mounting.FREE:  # held by a post holder or pedestal
            assert placed.footprints[Layer.BASE].area > 0
        if type_id != "base":
            assert placed.footprints[Layer.OPTIC].area > 0
            stops_only = unit.structural or unit.category == "Beam dumps"  # framing and dumps stop beams
            assert placed.build.blockers if stops_only else placed.build.elements


def test_mount_bore_is_open_at_beam_height():
    section = PARTS["KM100"].beam_section(0.0)
    assert not section.contains(Point(-0.1, 0.0))  # the optic bore
    assert section.contains(Point(-0.1, 0.7))  # plate material beside it


def test_right_angle_rotation_of_primitives():
    box = Box((0, 0, 0), (1, 2, 3))
    rolled = transform(box, rotation_matrix("x", 90))
    assert rolled.lo == pytest.approx((0, -3, 0)) and rolled.hi == pytest.approx((1, 0, 2))
    cyl = Cylinder("y", (0, 1, 0), 0.5, 2.0)
    assert transform(cyl, rotation_matrix("x", 90)).axis == "z"


def test_sprite_render_and_disk_cache(tmp_path):
    prims = PARTS["KM100"].oriented("bottom").prims
    sprite = render_sprite(prims, ppi=60)
    assert sprite.rgba.dtype == np.uint8 and sprite.rgba.shape[2] == 4
    assert sprite.rgba[..., 3].max() == 255
    assert sprite.shadow.max() > 0.5
    cache = SpriteCache(tmp_path, ppi=60)
    first = cache.get("KM100", prims)
    assert list(tmp_path.glob("KM100-*.npz"))
    reloaded = SpriteCache(tmp_path, ppi=60).get("KM100", prims)
    assert np.array_equal(first.rgba, reloaded.rgba)
    assert reloaded.x0 == pytest.approx(first.x0)


@pytest.mark.parametrize("make", ["rail", "rod"])
def test_stretched_sprite_matches_a_full_render(make):
    """Uniform parts are drawn from a 1" piece stretched to length: pixel for pixel the same."""
    from olayout.catalog import parts as P
    from olayout.catalog.primitives import transform_all
    from olayout.render.sprites import stretch_sprite

    if make == "rail":  # runs from its origin
        def prims(length):
            return P.tslot_rail_prims(length, 0.0)
        template_span, span = (0.0, 1.0), (0.0, 3.5)
    else:  # a cage rod is centred on its origin
        def prims(length):
            return transform_all(P.cage_rod_prims(length), np.eye(3), (0.0, 0.0, 0.59))
        template_span, span = (-0.5, 0.5), (-1.75, 1.75)
    full = render_sprite(prims(span[1] - span[0]), ppi=100)
    stretched = stretch_sprite(render_sprite(prims(1.0), ppi=100), template_span, span)
    assert stretched.rgba.shape == full.rgba.shape and stretched.x0 == pytest.approx(full.x0)
    assert np.abs(stretched.rgba.astype(int) - full.rgba.astype(int)).max() <= 1
    assert np.abs(stretched.shadow - full.shadow).max() < 1e-3
