import math

import pytest

from olayout.optics.gaussian import GaussianBeam, aperture_transmission, mirror_focal_lengths

W0 = 1.0e-3  # any consistent unit (here metres)
LAM = 1.0e-6


def test_rayleigh_range_and_spot_size():
    beam = GaussianBeam.from_waist(W0, LAM)
    z_r = math.pi * W0**2 / LAM
    assert beam.rayleigh_range == pytest.approx(z_r)
    assert beam.radius == pytest.approx(W0)
    assert beam.propagate(z_r).radius == pytest.approx(math.sqrt(2) * W0)
    assert beam.propagate(z_r).curvature_radius == pytest.approx(2 * z_r)
    assert math.isinf(beam.curvature_radius)
    assert beam.propagate(z_r).gouy_phase == pytest.approx(math.pi / 4)


def test_waist_position_is_preserved_by_free_space():
    beam = GaussianBeam.from_waist(W0, LAM, distance_from_waist=-0.3)  # waist 0.3 ahead
    after = beam.propagate(0.3)
    assert after.distance_from_waist == pytest.approx(0.0, abs=1e-12)
    assert after.radius == pytest.approx(W0)


def test_m2_scales_divergence():
    ideal = GaussianBeam.from_waist(W0, LAM)
    rough = GaussianBeam.from_waist(W0, LAM, m2=2.0)
    assert rough.rayleigh_range == pytest.approx(ideal.rayleigh_range / 2)
    assert rough.divergence == pytest.approx(2 * ideal.divergence)
    assert rough.waist_radius == pytest.approx(W0)


def test_thin_lens_focuses_collimated_beam_near_f():
    f = 0.1
    beam = GaussianBeam.from_waist(5e-3, LAM).thin_lens(f)  # zR = 78 m >> f
    waist_distance = -beam.distance_from_waist
    assert waist_distance == pytest.approx(f, rel=1e-4)
    assert beam.waist_radius == pytest.approx(LAM * f / (math.pi * 5e-3), rel=1e-3)


def test_abcd_free_space_matches_propagate():
    beam = GaussianBeam.from_waist(W0, LAM, 0.2)
    assert beam.abcd(1, 0.5, 0, 1).q == pytest.approx(beam.propagate(0.5).q)


def test_invalid_beam_rejected():
    with pytest.raises(ValueError):
        GaussianBeam.from_waist(0.0, LAM)


def test_mirror_focal_lengths_astigmatism():
    assert mirror_focal_lengths(100.0, 0.0) == pytest.approx((50.0, 50.0))
    f_t, f_s = mirror_focal_lengths(100.0, 60.0)
    assert f_t == pytest.approx(25.0)
    assert f_s == pytest.approx(100.0)
    assert all(math.isinf(f) for f in mirror_focal_lengths(math.inf, 30.0))


def test_aperture_transmission():
    assert aperture_transmission(1.0, 1.0) == pytest.approx(math.erf(math.sqrt(2)))
    assert aperture_transmission(1.0, 100.0) == pytest.approx(1.0)
    assert aperture_transmission(1.0, 1.0, offset=50.0) == pytest.approx(0.0, abs=1e-12)
