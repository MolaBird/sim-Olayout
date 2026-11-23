"""Gaussian beam propagation with the complex beam parameter q.

``q = z + i*zR`` where ``z`` is the distance *past* the waist and ``zR`` the
Rayleigh range.  Any consistent length unit works; the engine uses inches.

Beams with a quality factor M^2 > 1 are handled with the "embedded Gaussian"
convention: the same q algebra, with the wavelength scaled by M^2 when
converting between q and spot size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class GaussianBeam:
    """The state of a round (single-axis) Gaussian beam at one plane."""

    q: complex
    wavelength: float
    m2: float = 1.0

    @classmethod
    def from_waist(
        cls, waist_radius: float, wavelength: float, distance_from_waist: float = 0.0, m2: float = 1.0
    ) -> GaussianBeam:
        """Beam whose waist of radius ``waist_radius`` lies ``distance_from_waist``
        *upstream* of the current plane (negative: the waist is still ahead)."""
        if waist_radius <= 0 or wavelength <= 0 or m2 < 1.0:
            raise ValueError("waist, wavelength must be > 0 and M^2 >= 1")
        z_r = math.pi * waist_radius**2 / (m2 * wavelength)
        return cls(complex(distance_from_waist, z_r), wavelength, m2)

    # ------------------------------------------------------------ properties
    @property
    def effective_wavelength(self) -> float:
        return self.wavelength * self.m2

    @property
    def rayleigh_range(self) -> float:
        return self.q.imag

    @property
    def distance_from_waist(self) -> float:
        """Signed distance from the waist (positive: waist is behind us)."""
        return self.q.real

    @property
    def waist_radius(self) -> float:
        return math.sqrt(self.rayleigh_range * self.effective_wavelength / math.pi)

    @property
    def radius(self) -> float:
        """1/e^2 intensity radius at this plane."""
        inv = 1.0 / self.q
        return math.sqrt(-self.effective_wavelength / (math.pi * inv.imag))

    @property
    def curvature_radius(self) -> float:
        """Wavefront radius of curvature (inf at the waist)."""
        inv_real = (1.0 / self.q).real
        return math.inf if abs(inv_real) < 1e-300 else 1.0 / inv_real

    @property
    def gouy_phase(self) -> float:
        """Gouy phase in radians relative to the waist."""
        return math.atan2(self.q.real, self.q.imag)

    @property
    def divergence(self) -> float:
        """Far-field half-angle divergence in radians."""
        return self.waist_radius / self.rayleigh_range

    # ------------------------------------------------------------ operations
    def propagate(self, distance: float) -> GaussianBeam:
        return replace(self, q=self.q + distance)

    def abcd(self, a: float, b: float, c: float, d: float) -> GaussianBeam:
        q_out = (a * self.q + b) / (c * self.q + d)
        if q_out.imag <= 0:
            raise ValueError("ABCD transform produced a non-physical beam")
        return replace(self, q=q_out)

    def thin_lens(self, focal_length: float) -> GaussianBeam:
        """Thin lens (positive focal length converges); inf = no-op."""
        if math.isinf(focal_length):
            return self
        return self.abcd(1.0, 0.0, -1.0 / focal_length, 1.0)

    def radius_at(self, distance: float) -> float:
        return self.propagate(distance).radius


def mirror_focal_lengths(radius_of_curvature: float, incidence_deg: float) -> tuple[float, float]:
    """Tangential and sagittal focal lengths of a curved mirror hit off-axis.

    A concave mirror has a positive radius of curvature.  Returns
    ``(f_tangential, f_sagittal)``; both are ``inf`` for a flat mirror.
    """
    if math.isinf(radius_of_curvature) or radius_of_curvature == 0:
        return math.inf, math.inf
    cos_t = max(math.cos(math.radians(incidence_deg)), 1e-6)
    f = radius_of_curvature / 2.0
    return f * cos_t, f / cos_t


def aperture_transmission(radius: float, aperture_half_width: float, offset: float = 0.0) -> float:
    """Fraction of a Gaussian's 1D profile passing a slit ``[-a, a]`` when the
    beam centre is displaced by ``offset`` (a quick clipping estimate)."""
    if radius <= 0:
        return 1.0 if abs(offset) <= aperture_half_width else 0.0
    k = math.sqrt(2.0) / radius
    return 0.5 * (math.erf(k * (aperture_half_width - offset)) + math.erf(k * (aperture_half_width + offset)))
