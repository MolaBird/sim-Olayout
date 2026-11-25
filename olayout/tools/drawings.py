"""Machining drawings for the custom parts, as SVG.

    python -m olayout.tools.drawings [--out DIR] [--beam-height INCHES]

Draws dimensioned views of the parts the v3 layout needs from a machine
shop: the BE02R riser, the MRAK25 knife-edge holder, the beam block and the
dual-pinhole target.  The shapes come from the constants the catalog models
the parts with (``olayout.catalog.parts``), so a drawing cannot drift from
the part the simulator uses.  Only machining details (screw clearances,
counterbores, tap depths) are defined here.

The sheets are not to scale: work from the dimensions.  Inches unless marked.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from olayout import paths
from olayout.catalog import parts as P

MM = 1 / 25.4
SCALE = 100.0  # sheet pixels per model inch, before a view's zoom
FONT = 'font-family="DejaVu Sans, Helvetica, Arial, sans-serif"'
CRITICAL = "◆"  # ◆ marks a critical dimension
DIA = "Ø"

LINE_STYLES = {
    "visible": 'stroke="#111" stroke-width="1.7" fill="none"',
    "hidden": 'stroke="#111" stroke-width="0.9" stroke-dasharray="6 4" fill="none"',
    "centre": 'stroke="#2a62c9" stroke-width="0.8" stroke-dasharray="18 4 3 4" fill="none"',
    "phantom": 'stroke="#8a8a8a" stroke-width="1.0" stroke-dasharray="14 4 3 4 3 4" fill="none"',
    "thin": 'stroke="#111" stroke-width="0.8" fill="none"',
    "beam": 'stroke="#d0202a" stroke-width="1.6" fill="none"',
    "section": 'stroke="#111" stroke-width="1.7" fill="#d9d9d9"',
    "cut": 'stroke="#111" stroke-width="1.7" fill="#ffffff"',
}
BEAM_RED, CENTRE_BLUE, PHANTOM_GREY = "#d0202a", "#2a62c9", "#777"

# ---- machining details (not in the catalog model)
TAP_8_32 = {"drill": 0.136, "major": 0.164, "depth": 0.25}  # #29 drill
TAP_4_40 = {"drill": 0.089, "major": 0.112, "depth": 0.25}  # #43 drill
CLEAR_1_4 = 0.257  # F drill: close fit for 1/4"-20
CBORE_1_4 = 0.42  # for a 1/4"-20 socket head
RISER_BOLT_FLOOR = 0.50  # counterbore down to this floor: a 1.00"-long screw then takes 0.50" of table thread
CLEAR_8 = 0.177  # #16 drill: close fit for 8-32
CBORE_8 = (0.28, 0.19)  # diameter, depth, for an 8-32 socket head
CLEAR_4 = 0.120  # #31 drill: clearance for 4-40
KEH_CLAMP_SCREW_Y = 0.28  # the clamp plate's two 4-40 screws, either side of the centre line
TARGET_WEB = 0.040  # the pinholes go through this web at the beam-side face
TARGET_CBORE = 4.0 * MM  # counterbores from the back, one per pinhole
TARGET_SPACING_MM = 7.84  # beam separation at the v3 target hole (8.5, 9.5), from the model
TARGET_HOLE_MM = 1.0
BLOCK_BEAM_Z = -P.BEAM_BLOCK[0][2]  # the beam crosses the block this far above its bottom
TARGET_BEAM_Z = -P.TARGET_PLATE[0][2]


# ==========================================================================
# Sheet and views
# ==========================================================================
@dataclass
class Sheet:
    """An SVG drawing sheet; coordinates in pixels, y down."""

    width: float
    height: float
    items: list[str] = field(default_factory=list)

    def add(self, element: str) -> None:
        self.items.append(element)

    def line(self, x0: float, y0: float, x1: float, y1: float, style: str = "thin") -> None:
        self.add(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" {LINE_STYLES[style]}/>')

    def text(self, x: float, y: float, s: str, size: float = 13, anchor: str = "start", weight: str = "normal",
             rotate: float = 0.0, colour: str = "#111") -> None:
        transform = f' transform="rotate({rotate:.0f} {x:.1f} {y:.1f})"' if rotate else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" '
                 f'fill="{colour}" {FONT}{transform}>{escape(s)}</text>')

    def arrow(self, x: float, y: float, angle: float) -> None:
        """A filled arrowhead with its tip at (x, y), pointing along ``angle`` (radians, y down)."""
        length, half = 9.0, 3.0
        ca, sa = math.cos(angle), math.sin(angle)
        bx, by = x - length * ca, y - length * sa
        pts = ((x, y), (bx + half * sa, by - half * ca), (bx - half * sa, by + half * ca))
        self.add(f'<polygon points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in pts)}" fill="#111"/>')

    def notes(self, x: float, y: float, title: str, lines: list[str], width_chars: int = 64) -> float:
        """A numbered notes block, wrapped; returns the y below it."""
        self.text(x, y, title, 14, weight="bold")
        y += 22
        for i, line in enumerate(lines, 1):
            row, rows = "", []
            for word in line.split():
                if row and len(row) + len(word) + 1 > width_chars:
                    rows.append(row)
                    row = word
                else:
                    row = f"{row} {word}".strip()
            rows.append(row)
            for j, r in enumerate(rows):
                self.text(x, y, (f"{i}. " if j == 0 else "    ") + r, 12.5)
                y += 17
            y += 4
        return y

    def title_block(self, x: float, y: float, rows: list[tuple[str, str]], width: float = 430) -> None:
        h = 22 * len(rows) + 8
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{h:.1f}" {LINE_STYLES["visible"]}/>')
        for i, (key, value) in enumerate(rows):
            yy = y + 22 * (i + 1)
            if i:
                self.line(x, yy - 16, x + width, yy - 16)
            self.text(x + 8, yy, key, 11, colour="#555")
            self.text(x + 112, yy, value, 13, weight="bold" if i == 0 else "normal")
        self.line(x + 104, y, x + 104, y + h)

    def render(self) -> str:
        body = "\n".join(self.items)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width:.0f}" height="{self.height:.0f}" '
                f'viewBox="0 0 {self.width:.0f} {self.height:.0f}">\n'
                f'<rect x="0" y="0" width="{self.width:.0f}" height="{self.height:.0f}" fill="#ffffff"/>\n'
                f"{body}\n</svg>\n")


@dataclass
class View:
    """One orthographic view: model coordinates (u right, v up, inches) mapped onto the sheet."""

    sheet: Sheet
    ox: float  # sheet position of the model origin
    oy: float
    zoom: float = 1.0  # small parts are drawn larger

    @property
    def px(self) -> float:
        """Sheet pixels per model inch."""
        return SCALE * self.zoom

    def p(self, u: float, v: float) -> tuple[float, float]:
        return self.ox + u * self.px, self.oy - v * self.px

    # ---- geometry
    def polyline(self, pts, style: str = "visible", closed: bool = True) -> None:
        coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in (self.p(u, v) for u, v in pts))
        tag = "polygon" if closed else "polyline"
        self.sheet.add(f'<{tag} points="{coords}" {LINE_STYLES[style]}/>')

    def rect(self, u0: float, v0: float, u1: float, v1: float, style: str = "visible") -> None:
        self.polyline(((u0, v0), (u1, v0), (u1, v1), (u0, v1)), style)

    def line(self, u0: float, v0: float, u1: float, v1: float, style: str = "visible") -> None:
        self.sheet.line(*self.p(u0, v0), *self.p(u1, v1), style)

    def circle(self, u: float, v: float, r: float, style: str = "visible") -> None:
        x, y = self.p(u, v)
        self.sheet.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r * self.px:.1f}" {LINE_STYLES[style]}/>')

    def centre_mark(self, u: float, v: float, r: float) -> None:
        reach = r + 8 / self.px
        self.line(u - reach, v, u + reach, v, "centre")
        self.line(u, v - reach, u, v + reach, "centre")

    def hole_side(self, u: float, v0: float, v1: float, r: float, style: str = "hidden") -> None:
        """A hole running along v, seen from the side: its two walls."""
        self.line(u - r, v0, u - r, v1, style)
        self.line(u + r, v0, u + r, v1, style)

    def hole_side_h(self, v: float, u0: float, u1: float, r: float, style: str = "hidden") -> None:
        """A hole running along u, seen from the side."""
        self.line(u0, v - r, u1, v - r, style)
        self.line(u0, v + r, u1, v + r, style)

    def beam_mark(self, u: float, v: float) -> None:
        """A beam seen end-on: a small circle with a cross."""
        self.circle(u, v, 6 / self.px, "thin")
        self.line(u - 11 / self.px, v, u + 11 / self.px, v, "thin")
        self.line(u, v - 11 / self.px, u, v + 11 / self.px, "thin")

    # ---- text
    def label(self, u: float, v: float, s: str, size: float = 13, anchor: str = "middle", dy: float = 0.0,
              weight: str = "normal", colour: str = "#111") -> None:
        x, y = self.p(u, v)
        self.sheet.text(x, y + dy, s, size, anchor, weight, colour=colour)

    def title(self, u: float, v: float, s: str) -> None:
        self.label(u, v, s, 14, weight="bold")

    def leader(self, u: float, v: float, dx: float, dy: float, lines: list[str], anchor: str = "start") -> None:
        """A note whose leader arrow touches the feature at (u, v); the text sits (dx, dy) pixels away."""
        x, y = self.p(u, v)
        ex, ey = x + dx, y + dy
        self.sheet.line(x, y, ex, ey)
        self.sheet.arrow(x, y, math.atan2(y - ey, x - ex))
        tx = ex + (4 if anchor == "start" else -4)
        for i, s in enumerate(lines):
            self.sheet.text(tx, ey + 4 + 16 * i, s, 12.5, anchor)

    def datum(self, u: float, v: float, letter: str, dx: float, dy: float) -> None:
        """A datum feature symbol: a filled triangle on the surface at (u, v), its letter in a box (dx, dy) away."""
        x, y = self.p(u, v)
        bx, by = x + dx, y + dy
        self.sheet.line(x, y, bx, by)
        self.sheet.add(f'<polygon points="{x - 6:.1f},{y:.1f} {x + 6:.1f},{y:.1f} {x:.1f},{y + (6 if dy > 0 else -6):.1f}" '
                       f'fill="#111"/>')
        self.sheet.add(f'<rect x="{bx - 10:.1f}" y="{by - 10:.1f}" width="20" height="20" {LINE_STYLES["cut"]}/>')
        self.sheet.text(bx, by + 5, letter, 14, "middle", "bold")

    # ---- dimensions
    def hdim(self, u0: float, u1: float, at_v: float, label: str, from_v: tuple[float, float] | None = None,
             outside: str = "right") -> None:
        """Horizontal dimension from u0 to u1, drawn at height ``at_v``; its
        extension lines start at the features' heights ``from_v``.  A label too
        long for the gap goes ``outside`` it, to the right or left."""
        (x0, y), (x1, _) = self.p(u0, at_v), self.p(u1, at_v)
        if from_v is not None:
            for x, v in ((x0, from_v[0]), (x1, from_v[1])):
                yf = self.p(0.0, v)[1]
                gap, over = (4, 6) if yf > y else (-4, -6)
                self.sheet.line(x, yf - gap, x, y - over)
        lo, hi = min(x0, x1), max(x0, x1)
        if hi - lo > 22:
            self.sheet.line(lo, y, hi, y)
            self.sheet.arrow(lo, y, math.pi)
            self.sheet.arrow(hi, y, 0.0)
        else:  # too short for arrows inside: point at the gap from outside
            self.sheet.line(lo - 16, y, hi + 16, y)
            self.sheet.arrow(lo, y, 0.0)
            self.sheet.arrow(hi, y, math.pi)
        if hi - lo > 7.2 * len(label) + 8:
            self.sheet.text((lo + hi) / 2, y - 5, label, 13, "middle")
        elif outside == "left":
            self.sheet.text(lo - 19, y + 4, label, 13, "end")
        else:
            self.sheet.text(hi + 19, y + 4, label, 13, "start")

    def vdim(self, v0: float, v1: float, at_u: float, label: str, from_u: tuple[float, float] | None = None,
             text_side: str = "left") -> None:
        """Vertical dimension from v0 to v1, drawn at ``at_u``."""
        (x, y0), (_, y1) = self.p(at_u, v0), self.p(at_u, v1)
        if from_u is not None:
            for y, u in ((y0, from_u[0]), (y1, from_u[1])):
                xf = self.p(u, 0.0)[0]
                gap, over = (4, 6) if xf < x else (-4, -6)
                self.sheet.line(xf + gap, y, x + over, y)
        lo, hi = min(y0, y1), max(y0, y1)
        if hi - lo > 22:
            self.sheet.line(x, lo, x, hi)
            self.sheet.arrow(x, lo, -math.pi / 2)
            self.sheet.arrow(x, hi, math.pi / 2)
        else:
            self.sheet.line(x, lo - 16, x, hi + 16)
            self.sheet.arrow(x, lo, math.pi / 2)
            self.sheet.arrow(x, hi, -math.pi / 2)
        mid = (lo + hi) / 2
        if hi - lo > 7.2 * len(label) + 8:
            dx = -6 if text_side == "left" else 16
            self.sheet.text(x + dx, mid, label, 13, "middle", rotate=-90)
        else:
            anchor, dx = ("end", -8) if text_side == "left" else ("start", 8)
            self.sheet.text(x + dx, mid + 4, label, 13, anchor)


def _title_rows(title: str, part_no: str, qty: str, material: str, finish: str) -> list[tuple[str, str]]:
    return [
        ("TITLE", title),
        ("PART NO.", part_no),
        ("QTY", qty),
        ("MATERIAL", material),
        ("FINISH", finish),
        ("UNITS", "inch (mm where marked)"),
        ("TOLERANCES", f".XX ±.01   .XXX ±.005   {CRITICAL} ±.002"),
        ("SCALE", "not to scale: use the dimensions"),
        ("PROJECT", "sim-Olayout, breadboard layout v3"),
        ("DATE", date.today().isoformat()),
    ]


# ==========================================================================
# BE02R riser
# ==========================================================================
@dataclass(frozen=True)
class RiserGeometry:
    """The riser in its own frame: origin at the block's front-left-bottom
    corner, X along the beam, Y toward the wall, Z up from the table."""

    length: float
    depth: float
    height: float  # H, from the table to the BE02R's base
    wall_x: tuple[float, float]
    wall_y: float  # the wall's inner face (datum A)
    wall_height: float  # above the block's top face
    bolts: tuple[tuple[float, float], ...]
    side_holes: tuple[tuple[float, float], ...]  # (X, height above the top face)
    mirror1: tuple[float, float]  # where the input axis meets mirror 1 (the BE02R's datum)
    axis_height: float  # the beam axis above the top face
    flange: tuple[tuple[float, float], ...]

    @classmethod
    def from_catalog(cls, beam_height: float) -> RiserGeometry:
        (x0, y0), _, (x1, y1), _ = P.RISER_OUTLINE
        bottom = P.BE02R_BOTTOM  # the BE02R's base, below its axis (negative)
        return cls(
            length=x1 - x0,
            depth=y1 - y0,
            height=beam_height + bottom,
            wall_x=(P.RISER_WALL_X[0] - x0, P.RISER_WALL_X[1] - x0),
            wall_y=P.RISER_WALL_Y[0] - y0,
            wall_height=P.RISER_WALL_TOP - bottom,
            bolts=tuple((bx - x0, by - y0) for bx, by in P.RISER_BOLTS),
            side_holes=tuple((hx - x0, hz - bottom) for hx, hz in P.RISER_SIDE_HOLES),
            mirror1=(-x0, -y0),
            axis_height=-bottom,
            flange=tuple((fx - x0, fy - y0) for fx, fy in P.BE02R_FLANGE),
        )


def riser_sheet(beam_height: float = 3.5) -> Sheet:
    g = RiserGeometry.from_catalog(beam_height)
    sheet = Sheet(1460, 1180)
    L, D, H = g.length, g.depth, g.height
    (wx0, wx1), wy, wh = g.wall_x, g.wall_y, g.wall_height
    az = g.axis_height
    (bx0, by), (bx1, _) = g.bolts
    xs = sorted({x for x, _ in g.side_holes})
    zs = sorted({z for _, z in g.side_holes})
    r_bolt, r_cb = CLEAR_1_4 / 2, CBORE_1_4 / 2
    r8, (cb8, cb8_depth) = CLEAR_8 / 2, CBORE_8
    m1x, m1y = g.mirror1

    # ---- TOP view (top left)
    top = View(sheet, 170, 80 + (D + 0.55) * SCALE)
    top.rect(0, 0, L, D)
    top.rect(wx0, wy, wx1, D)
    top.polyline(g.flange, "phantom")
    for bx, b_y in g.bolts:
        top.circle(bx, b_y, r_cb)
        top.circle(bx, b_y, r_bolt)
        top.centre_mark(bx, b_y, r_cb)
    for x in xs:
        top.hole_side(x, wy, D - cb8_depth, r8)
        top.hole_side(x, D - cb8_depth, D, cb8 / 2)
    top.line(-0.6, m1y, m1x, m1y, "beam")
    top.sheet.arrow(*top.p(-0.1, m1y), 0.0)
    top.label(-0.6, m1y, "BEAM IN", 11, "start", dy=-6, colour=BEAM_RED)
    top.line(m1x - 0.07, m1y - 0.07, m1x + 0.07, m1y + 0.07, "thin")
    top.line(m1x - 0.07, m1y + 0.07, m1x + 0.07, m1y - 0.07, "thin")
    top.label(m1x + 0.1, m1y + 0.08, "mirror 1", 11, "start", colour=PHANTOM_GREY)
    top.datum(wx1 - 0.3, wy, "A", 0, 26)
    top.hdim(0, L, -0.5, f"{L:.3f}", (0, 0))
    top.hdim(0, bx0, -0.25, f"{bx0:.3f}", (by, by))
    top.hdim(bx0, bx1, -0.25, f"{bx1 - bx0:.3f} {CRITICAL}", (by, by))
    top.vdim(0, D, -0.75, f"{D:.3f}", (0, 0))
    top.vdim(by, wy, L + 0.3, f"{wy - by:.4f} {CRITICAL}", (bx1, wx1), text_side="right")
    top.vdim(wy, D, L + 0.3, f"{D - wy:.3f}", (wx1, wx1), text_side="right")
    top.vdim(m1y, wy, L + 0.75, f"{wy - m1y:.4f} REF", (m1x, wx1), text_side="right")
    top.hdim(0, wx0, D + 0.25, f"{wx0:.3f}", (D, D), outside="left")
    top.hdim(wx0, wx1, D + 0.25, f"{wx1 - wx0:.3f}", (D, D))
    top.hdim(0, m1x, D + 0.55, f"{m1x:.3f} REF", (D, D))
    top.title(L / 2, -0.85, "TOP")

    # ---- SIDE view (from +X), top right
    side = View(sheet, 640, 110 + (H + wh) * SCALE)
    side.rect(0, 0, D, H)
    side.rect(wy, H, D, H + wh)
    for z in zs:
        side.hole_side_h(H + z, wy, D - cb8_depth, r8)
        side.hole_side_h(H + z, D - cb8_depth, D, cb8 / 2)
        side.line(wy - 0.1, H + z, D + 0.1, H + z, "centre")
    side.hole_side(by, 0, RISER_BOLT_FLOOR, r_bolt)
    side.hole_side(by, RISER_BOLT_FLOOR, H, r_cb)
    side.line(by, -0.1, by, H + 0.1, "centre")
    side.beam_mark(m1y, H + az)
    side.label(m1y, H + az, "beam axis", 11, dy=-16, colour=CENTRE_BLUE)
    side.datum(wy, H + 0.6 * wh, "A", -34, 0)
    side.hdim(0, D, -0.3, f"{D:.3f}", (0, 0))
    side.hdim(wy, D, H + wh + 0.25, f"{D - wy:.3f}", (H + wh, H + wh), outside="left")
    side.hdim(by, wy, -0.6, f"{wy - by:.4f} {CRITICAL}", (0, 0))
    side.title(D / 2, -0.95, "SIDE (from +X)")

    # ---- FRONT view (from the beam side, -Y), below the top view
    front = View(sheet, top.ox, 620 + (H + wh + 0.4) * SCALE)
    front.rect(0, 0, L, H)
    front.rect(wx0, H, wx1, H + wh)
    for x in xs:
        for z in zs:
            front.circle(x, H + z, r8)
            front.circle(x, H + z, cb8 / 2, "hidden")
            front.centre_mark(x, H + z, cb8 / 2)
    for bx in (bx0, bx1):
        front.hole_side(bx, 0, RISER_BOLT_FLOOR, r_bolt)
        front.hole_side(bx, RISER_BOLT_FLOOR, H, r_cb)
        front.line(bx - r_cb, RISER_BOLT_FLOOR, bx + r_cb, RISER_BOLT_FLOOR, "hidden")
        front.line(bx, -0.1, bx, H + 0.1, "centre")
    front.line(-0.3, H + az, L + 1.0, H + az, "centre")
    front.label(L + 1.05, H + az, "BEAM AXIS", 11, "start", dy=4, colour=CENTRE_BLUE)
    front.vdim(0, H, L + 0.35, f"H = h − {az:.3f} {CRITICAL}", (L, L), text_side="right")
    front.vdim(H, H + wh, L + 0.35, f"{wh:.3f}", (wx1, wx1), text_side="right")
    front.vdim(0, H + az, L + 0.8, "h (beam height) REF", (L + 0.3, L + 0.3), text_side="right")
    front.vdim(H, H + zs[0], -0.25, f"{zs[0]:.3f}", (wx0, xs[0]))
    front.vdim(H + zs[0], H + zs[1], -0.25, f"{zs[1] - zs[0]:.3f}", (xs[0], xs[0]))
    front.vdim(0, RISER_BOLT_FLOOR, -0.25, f"{RISER_BOLT_FLOOR:.2f}", (bx0, bx0))
    front.hdim(0, xs[0], H + wh + 0.25, f"{xs[0]:.3f}", (H, H + wh))
    front.hdim(xs[0], xs[1], H + wh + 0.25, f"{xs[1] - xs[0]:.3f}", (H + wh, H + wh))
    front.leader(xs[1] + 0.06, H + zs[1] + 0.06, 215, -20,
                 [f"4X {DIA}{CLEAR_8:.3f} (#16) THRU,", f"C'BORE {DIA}{cb8:.2f} x {cb8_depth:.2f} DP on the far side,",
                  "for 8-32 x 3/8 SHCS"])
    front.leader(bx1 + r_cb, 0.3, 250, 50,
                 [f"2X {DIA}{CLEAR_1_4:.3f} (F) THRU, C'BORE {DIA}{CBORE_1_4:.2f}",
                  f"from the top down to a {RISER_BOLT_FLOOR:.2f} floor,", "for 1/4\"-20 x 1.00 SHCS into the table"])
    front.title(L / 2, -0.4, "FRONT (beam side)")

    # ---- title block and notes
    sheet.title_block(1000, 40, _title_rows("BE02R riser", "OLY-001", "2 (BX7, BX8), identical",
                                            "6061-T6 aluminium", "black anodize"))
    examples = "; ".join(f"h = {h:.3f}: H = {h - az:.3f}" for h in (3.0, 3.5, 4.0))
    sheet.notes(1000, 300, "NOTES", [
        "Puts the BE02R's axis at the beam height h and fixes its position. The BE02R stands on the top face "
        "and its side plate bolts to the wall with four 8-32 screws; the riser bolts to the table with two "
        "1/4\"-20 screws under the BE02R (bolt the riser down first).",
        f"Height H = h - {az:.3f} (the BE02R's axis is 25.0 mm above its base). {examples}. "
        "Make both risers in one setup so their heights match within .001.",
        f"Datum A, the wall face: flat within .001 and square to the bottom within .002. {CRITICAL} dimensions "
        "place the beam: hold them to ±.002.",
        "Mirror 1 sits over the beam's hole row, on a whole-inch x: the bolts then land on table holes 1.000 "
        "in front of the beam row, 0.500 either side of mirror 1.",
        "The 8-32 pattern is the BE02R side plate's, measured from the vendor STEP model. Check it against "
        "your BE02R with transfer screws before drilling.",
        "Break all sharp edges. The phantom outline is the BE02R's flange, for reference only.",
    ])
    return sheet


# ==========================================================================
# Knife-edge holder
# ==========================================================================
def knife_edge_holder_sheet() -> Sheet:
    sheet = Sheet(1460, 1120)
    lead, back = P.KNIFE_EDGE_LEAD, P.KEH_FENCE_X
    rear = back - P.KEH_FENCE_T
    front = rear + P.KEH_BASE_LENGTH
    w = P.KEH_HALF_WIDTH
    base_t, prism_h = P.KEH_BASE_T, P.MRAK25_HEIGHT
    fence_top = base_t + prism_h + P.KEH_FENCE_CLEARANCE
    clamp_l, clamp_w, clamp_t = P.KEH_CLAMP
    screw_x = rear + P.KEH_FENCE_T / 2
    setscrew_x = back + P.KEH_SETSCREW_X
    beam_z = base_t + prism_h / 2
    reach = P.MRAK25_LEG / math.sqrt(2)  # the prism's back corners, either side
    zoom = 1.6

    # ---- TOP view
    top = View(sheet, 170 + 1.6 * SCALE * zoom, 70 + 1.35 * SCALE * zoom, zoom)
    top.rect(rear, -w, front, w)
    top.rect(rear, -w, back, w)
    top.polyline(((lead, 0), (back, reach), (back, -reach)), "phantom")
    top.circle(0, 0, TAP_8_32["major"] / 2, "hidden")
    top.centre_mark(0, 0, 0.1)
    for y in (-KEH_CLAMP_SCREW_Y, KEH_CLAMP_SCREW_Y):
        top.circle(screw_x, y, TAP_4_40["major"] / 2)
        top.centre_mark(screw_x, y, 0.03)
    for sgn in (1, -1):
        top.line(0, sgn * 1.2, 0, sgn * lead, "beam")
        top.line(0, sgn * lead, 1.0, sgn * lead, "beam")
        top.sheet.arrow(*top.p(1.0, sgn * lead), 0.0)
        top.sheet.arrow(*top.p(0, sgn * 0.8), math.pi / 2 * sgn)
    top.label(0.06, 1.12, "beams in, along the post's hole column", 11, "start", colour=BEAM_RED)
    top.label(0.5, lead, "beams out", 11, "middle", dy=-6, colour=BEAM_RED)
    top.leader(0, 0, -150, 150, ["8-32 TAP x .25 DP from the bottom,", "on the post axis"], anchor="end")
    top.leader(screw_x, KEH_CLAMP_SCREW_Y, -110, -70, ["2X 4-40 TAP x .25 DP", "in the fence top"], anchor="end")
    top.hdim(back, 0, w + 0.22, f"{-back:.4f} ({-back / MM:.2f} mm) {CRITICAL}", (w, 0.0))
    top.hdim(rear, back, w + 0.22, f"{P.KEH_FENCE_T:.3f}", (w, w), outside="left")
    top.hdim(0, lead, -w - 0.2, f"{lead:.4f} ({lead / MM:.2f} mm) REF", (0.0, 0.0))
    top.hdim(rear, front, -w - 0.45, f"{P.KEH_BASE_LENGTH:.3f}", (-w, -w))
    top.vdim(-w, w, rear - 0.25, f"{2 * w:.3f}", (rear, rear))
    top.vdim(-KEH_CLAMP_SCREW_Y, KEH_CLAMP_SCREW_Y, rear - 0.55, f"{2 * KEH_CLAMP_SCREW_Y:.3f}",
             (screw_x, screw_x))
    top.vdim(-lead, lead, 1.15, "9.00 mm REF", (1.0, 1.0), text_side="right")
    top.title(-0.2, -w - 0.8, "TOP")

    # ---- FRONT view (from -Y)
    front_v = View(sheet, top.ox, top.oy + (w + 1.0 + fence_top + 0.45) * SCALE * zoom, zoom)
    front_v.rect(rear, 0, front, base_t)
    front_v.rect(rear, base_t, back, fence_top)
    front_v.rect(back, base_t, lead, base_t + prism_h, "phantom")
    front_v.rect(rear, fence_top, rear + clamp_l, fence_top + clamp_t, "phantom")
    front_v.rect(setscrew_x - 0.056, base_t + prism_h, setscrew_x + 0.056, fence_top + clamp_t + 0.1, "phantom")
    front_v.hole_side(0, 0, TAP_8_32["depth"], TAP_8_32["drill"] / 2)
    front_v.hole_side(0, 0, TAP_8_32["depth"], TAP_8_32["major"] / 2, "thin")
    front_v.line(0, -0.1, 0, base_t + 0.08, "centre")
    front_v.hole_side(screw_x, fence_top - TAP_4_40["depth"], fence_top, TAP_4_40["drill"] / 2)
    front_v.line(rear - 0.15, beam_z, front + 0.15, beam_z, "centre")
    front_v.label(front + 0.2, beam_z, "BEAM HEIGHT h (prism mid-height)", 11, "start", dy=4,
                  colour=CENTRE_BLUE)
    front_v.label(setscrew_x + 0.1, fence_top + clamp_t + 0.12, "clamp plate + nylon-tip setscrew (phantom)",
                  11, "start", colour=PHANTOM_GREY)
    front_v.vdim(0, base_t, front + 0.22, f"{base_t:.3f}", (front, front), text_side="right")
    front_v.vdim(base_t, fence_top, rear - 0.25, f"{fence_top - base_t:.3f}", (rear, rear))
    front_v.vdim(0, beam_z, rear - 0.6, f"{beam_z:.3f} REF", (rear, rear))
    front_v.title(-0.2, -0.4, "FRONT (from the beam side)")

    # ---- CLAMP PLATE (separate part), seen from above; origin at its rear edge
    plate = View(sheet, 1060, 930, zoom)
    plate.rect(0, -clamp_w / 2, clamp_l, clamp_w / 2)
    for y in (-KEH_CLAMP_SCREW_Y, KEH_CLAMP_SCREW_Y):
        plate.circle(screw_x - rear, y, CLEAR_4 / 2)
    plate.circle(setscrew_x - rear, 0, TAP_4_40["major"] / 2)
    plate.circle(setscrew_x - rear, 0, TAP_4_40["drill"] / 2, "thin")
    plate.hdim(0, screw_x - rear, -clamp_w / 2 - 0.18, f"{screw_x - rear:.3f}", (-clamp_w / 2, -clamp_w / 2))
    plate.hdim(0, setscrew_x - rear, -clamp_w / 2 - 0.38, f"{setscrew_x - rear:.3f}",
               (-clamp_w / 2, -clamp_w / 2))
    plate.hdim(0, clamp_l, clamp_w / 2 + 0.18, f"{clamp_l:.3f}", (clamp_w / 2, clamp_w / 2))
    plate.vdim(-clamp_w / 2, clamp_w / 2, clamp_l + 0.22, f"{clamp_w:.3f}", (clamp_l, clamp_l),
               text_side="right")
    plate.vdim(-KEH_CLAMP_SCREW_Y, KEH_CLAMP_SCREW_Y, -0.22, f"{2 * KEH_CLAMP_SCREW_Y:.3f}", (0.0, 0.0))
    plate.leader(screw_x - rear, KEH_CLAMP_SCREW_Y, 90, -40, [f"2X {DIA}{CLEAR_4:.3f} (#31) THRU"])
    plate.leader(setscrew_x - rear, 0, 110, 30, ["4-40 TAP THRU, for a", "nylon-tip setscrew"])
    plate.title(clamp_l / 2, -clamp_w / 2 - 0.62, f"CLAMP PLATE, {clamp_t:.3f} THICK")

    sheet.title_block(1000, 40, _title_rows("Knife-edge holder for MRAK25-P01", "OLY-002",
                                            "1 (KE1): body + clamp plate", "6061-T6 aluminium", "black anodize"))
    sheet.notes(1000, 300, "NOTES", [
        "Holds the MRAK25-P01 knife-edge prism on a 1/2\" post in a PH2, its edge "
        f"{lead / MM:.2f} mm ahead of the post axis. Beams fed along the post's own hole column, one from each "
        f"side, then land {lead / MM:.2f} mm either side of the edge and leave {2 * lead / MM:.1f} mm apart.",
        f"{CRITICAL} The fence face (the prism's hypotenuse) is {-back:.4f} ({-back / MM:.2f} mm) behind the 8-32 "
        f"axis: the prism's apex is {reach / MM:.2f} mm in front of its hypotenuse (25.0 mm legs). Measure your "
        "prism; if it differs, move the fence face to keep the edge 4.50 mm ahead.",
        "Fence face flat within .001 and square to the bottom within .002: it keeps the edge vertical.",
        "Body = base + fence in one piece. The fence stands .031 above the prism, so the clamp plate never "
        "touches the glass; a nylon-tipped 4-40 setscrew presses the prism down (finger tight).",
        "Assembly: hypotenuse against the fence, prism centred on a centre line scribed on the base, clamp "
        "plate on, setscrew down. Never touch the silvered legs.",
        f"Post: top at h - {beam_z:.3f} (the prism's mid-height at the beam height h).",
        "Phantom lines: the MRAK25-P01 prism and, in the front view, the clamp plate with its setscrew.",
    ])
    return sheet


# ==========================================================================
# Beam block
# ==========================================================================
def beam_block_sheet() -> Sheet:
    sheet = Sheet(1460, 700)
    (x0, y0, z0), (x1, y1, z1) = P.BEAM_BLOCK
    t, w, h = x1 - x0, y1 - y0, z1 - z0
    zoom = 2.0

    face = View(sheet, 200 + w / 2 * SCALE * zoom, 120 + h * SCALE * zoom, zoom)
    face.rect(-w / 2, 0, w / 2, h)
    face.hole_side(0, 0, TAP_8_32["depth"], TAP_8_32["drill"] / 2)
    face.hole_side(0, 0, TAP_8_32["depth"], TAP_8_32["major"] / 2, "thin")
    face.line(0, -0.08, 0, TAP_8_32["depth"] + 0.08, "centre")
    face.line(-w / 2 - 0.1, BLOCK_BEAM_Z, w / 2 + 0.1, BLOCK_BEAM_Z, "centre")
    face.label(w / 2 + 0.12, BLOCK_BEAM_Z, "beam height h", 11, "start", dy=4, colour=CENTRE_BLUE)
    face.hdim(-w / 2, w / 2, -0.18, f"{w:.3f}", (0, 0))
    face.vdim(0, h, -w / 2 - 0.18, f"{h:.3f}", (-w / 2, -w / 2))
    face.vdim(0, BLOCK_BEAM_Z, -w / 2 - 0.45, f"{BLOCK_BEAM_Z:.3f} REF", (-w / 2, -w / 2))
    face.leader(0.0, TAP_8_32["depth"], 130, 45, ["8-32 TAP x .25 DP,", "centred in the bottom face"])
    face.title(0, -0.42, "FACE")

    side = View(sheet, face.ox + (w / 2 + 1.2) * SCALE * zoom, face.oy, zoom)
    side.rect(-t / 2, 0, t / 2, h)
    side.hole_side(0, 0, TAP_8_32["depth"], TAP_8_32["drill"] / 2)
    side.line(0, -0.08, 0, TAP_8_32["depth"] + 0.08, "centre")
    side.hdim(-t / 2, t / 2, -0.18, f"{t:.3f}", (0, 0))
    side.title(0, -0.42, "SIDE")

    sheet.title_block(1000, 40, _title_rows("Beam block", "OLY-003", "2 (BB1, BB2)", "6061-T6 aluminium",
                                            "bead blast, then black anodize"))
    sheet.notes(1000, 300, "NOTES", [
        "Stops the 8% strays the imaging pellicles BS4 and BS5 send back across the board (about 0.2 mW each).",
        "Bead blast before anodizing so the face scatters instead of reflecting. Break sharp edges.",
        f"Post: top at h - {BLOCK_BEAM_Z:.3f}. BB1 faces its stray square on; BB2 is turned 30 degrees so it "
        "clears BS5's mount (as placed in the layout).",
    ])
    return sheet


# ==========================================================================
# Dual-pinhole target
# ==========================================================================
def target_sheet(spacing_mm: float = TARGET_SPACING_MM, hole_mm: float = TARGET_HOLE_MM) -> Sheet:
    sheet = Sheet(1460, 900)
    (x0, y0, z0), (x1, y1, z1) = P.TARGET_PLATE
    t, w, h = x1 - x0, y1 - y0, z1 - z0
    half_gap = spacing_mm * MM / 2
    r_hole, r_cb = hole_mm * MM / 2, TARGET_CBORE / 2
    web = -t / 2 + TARGET_WEB  # the web's back face; the beams arrive on the -t/2 face
    zoom = 2.0

    face = View(sheet, 200 + w / 2 * SCALE * zoom, 110 + (h + 0.2) * SCALE * zoom, zoom)
    face.rect(-w / 2, 0, w / 2, h)
    for y in (-half_gap, half_gap):
        face.circle(y, TARGET_BEAM_Z, r_hole)
        face.circle(y, TARGET_BEAM_Z, r_cb, "hidden")
        face.line(y, TARGET_BEAM_Z - 0.12, y, TARGET_BEAM_Z + 0.12, "thin")
    face.line(-w / 2, TARGET_BEAM_Z, w / 2, TARGET_BEAM_Z, "thin")
    face.line(0, 0, 0, h, "thin")
    face.hole_side(0, 0, TAP_8_32["depth"], TAP_8_32["drill"] / 2)
    face.hdim(-w / 2, w / 2, -0.18, f"{w:.3f}", (0, 0))
    face.vdim(0, h, -w / 2 - 0.18, f"{h:.3f}", (-w / 2, -w / 2))
    face.vdim(0, TARGET_BEAM_Z, w / 2 + 0.18, f"{TARGET_BEAM_Z:.3f} {CRITICAL}", (w / 2, w / 2),
              text_side="right")
    face.hdim(-half_gap, half_gap, h + 0.18, f"{spacing_mm:.2f} mm {CRITICAL} (±.02 mm)",
              (TARGET_BEAM_Z, TARGET_BEAM_Z))
    face.leader(half_gap + 0.015, TARGET_BEAM_Z + 0.015, 170, -95,
                [f"2X {DIA}{hole_mm:.2f} mm THRU the web,", "symmetric about the centre line"])
    face.leader(0.0, TAP_8_32["depth"], -110, 62, ["8-32 TAP x .25 DP"], anchor="end")
    face.leader(-w / 2 + 0.15, TARGET_BEAM_Z, -50, -80, ["engraved lines,", ".005 deep"], anchor="end")
    face.title(0, -0.42, "FACE (toward the beams)")

    sec = View(sheet, face.ox, face.oy + (t / 2 + 0.75) * SCALE * zoom, zoom)
    sec.rect(-w / 2, -t / 2, w / 2, t / 2, "section")
    for y in (-half_gap, half_gap):
        sec.rect(y - r_cb, web, y + r_cb, t / 2, "cut")
        sec.rect(y - r_hole, -t / 2, y + r_hole, web, "cut")
    sec.label(0, -t / 2 - 0.08, "beams arrive on this face", 11, colour=BEAM_RED, dy=10)
    sec.hdim(-w / 2, w / 2, -t / 2 - 0.32, f"{w:.3f}", (-t / 2, -t / 2))
    sec.vdim(-t / 2, t / 2, -w / 2 - 0.18, f"{t:.3f}", (-w / 2, -w / 2))
    sec.vdim(-t / 2, web, w / 2 + 0.18, f"{TARGET_WEB:.3f} web", (w / 2, half_gap), text_side="right")
    sec.leader(half_gap + r_cb, t / 4, 150, -60, [f"2X {DIA}{TARGET_CBORE / MM:.1f} mm from the back,",
                                                 f"leaving the {TARGET_WEB:.3f} web"])
    sec.title(0, -t / 2 - 0.55, "SECTION A-A (through the pinholes, from above)")

    sheet.title_block(1000, 40, _title_rows("Dual-pinhole alignment target", "OLY-004", "1 (alignment tool)",
                                            "6061-T6 aluminium", "bead blast, clear anodize"))
    sheet.notes(1000, 300, "NOTES", [
        "Checks both beams at once on the common path after the knife edge: in hole (8.5, 9.5), face toward "
        f"KE1, each beam passes its pinhole. The beams are {spacing_mm:.2f} mm apart there (model), centred on "
        "the hole row.",
        "Clear anodize keeps the face light, so the HeNe spot shows around each pinhole. Deburr the pinholes; "
        "the face toward the beams must be burr-free.",
        "Engrave one line through both pinhole centres (the beam plane) and one on the centre line.",
        f"Post: top at h - {TARGET_BEAM_Z:.3f}. Turning the post a little moves the pinholes along the beams, "
        "not across them: no need to clock it precisely.",
        "Use it with the HeNe only; take it out for operation (the pinholes would clip the 2923 beams).",
    ])
    return sheet


# ==========================================================================
SHEETS = {
    "be02r-riser.svg": riser_sheet,
    "knife-edge-holder.svg": knife_edge_holder_sheet,
    "beam-block.svg": beam_block_sheet,
    "dual-pinhole-target.svg": target_sheet,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=paths.PROJECT_ROOT / "docs" / "drawings",
                        help="output folder (default: docs/drawings)")
    parser.add_argument("--beam-height", type=float, default=3.5,
                        help="beam height above the table, inches; only sets the riser's drawn proportions")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, make in SHEETS.items():
        sheet = make(args.beam_height) if make is riser_sheet else make()
        (args.out / name).write_text(sheet.render(), encoding="utf-8")
        print(f"wrote {args.out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
