"""Length units and human-friendly formatting.

The engine works in inches everywhere (layout *and* beam optics), so the
constants below convert *into* inches:  ``5 * MM`` is five millimetres
expressed in inches.
"""

from __future__ import annotations

import math
from fractions import Fraction

INCH = 1.0
MM = 1.0 / 25.4
CM = 10.0 * MM
UM = MM / 1000.0
NM = MM / 1.0e6


def to_mm(inches: float) -> float:
    return inches * 25.4


def from_mm(mm: float) -> float:
    return mm / 25.4


def format_inches(value: float, decimals: int = 3) -> str:
    """Decimal inches, e.g. ``1.250"``."""
    return f'{value:.{decimals}f}"'


def format_fraction(value: float, denominator: int = 64) -> str:
    """Nearest binary fractional inch, e.g. ``1-1/2"`` or ``-3/8"``.

    Values that are not within 1e-4" of a 1/64" step get a ``~`` prefix so
    the user knows the fraction is rounded.
    """
    sign = "-" if value < 0 else ""
    frac = Fraction(round(abs(value) * denominator), denominator)
    approx = "" if math.isclose(float(frac), abs(value), abs_tol=1e-4) else "~"
    whole, rest = divmod(frac.numerator, frac.denominator)
    if rest == 0:
        return f'{approx}{sign}{whole}"'
    if whole == 0:
        return f'{approx}{sign}{rest}/{frac.denominator}"'
    return f'{approx}{sign}{whole}-{rest}/{frac.denominator}"'


def format_length(inches: float, decimals: int = 3) -> str:
    """Decimal inches with the metric value alongside: ``1.250" (31.75 mm)``."""
    return f"{format_inches(inches, decimals)} ({to_mm(inches):.2f} mm)"


def format_beam_size(inches: float) -> str:
    """Beam radii are conventionally quoted in mm or µm."""
    mm = to_mm(inches)
    if abs(mm) < 1.0:
        return f"{mm * 1000.0:.1f} µm"
    return f"{mm:.3f} mm"


def format_optical_distance(inches: float) -> str:
    """Distances that can be huge (Rayleigh range, radius of curvature)."""
    if math.isinf(inches):
        return "∞"
    if abs(inches) >= 1.0e4:
        return f"{to_mm(inches) / 1000.0:.3g} m"
    return f'{inches:.3f}" ({to_mm(inches):.1f} mm)'


def format_angle(degrees: float, decimals: int = 2) -> str:
    return f"{degrees:.{decimals}f}°"


def normalize_angle(degrees: float) -> float:
    """Map any angle to the half-open interval [0, 360)."""
    result = math.fmod(degrees, 360.0)
    if result < 0:
        result += 360.0
    # fmod can return 360.0 - tiny; snap values that print as 360 back to 0.
    return 0.0 if math.isclose(result, 360.0, abs_tol=1e-9) else result
