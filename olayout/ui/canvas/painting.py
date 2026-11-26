"""Low-level painting shared by canvas items and library thumbnails.

Coordinate note: the model has y pointing up; Qt items have y pointing
down.  Everything here takes model-frame geometry and paints it into an
item whose local frame is the unit frame with y flipped, i.e. a model point
(x, y) is drawn at (x, -y) and a model rotation ``a`` is ``rotate(-a)``.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)

from olayout.catalog.parts import Layer
from olayout.catalog import parts as P
from olayout.catalog.units import (
    Mounting,
    OpticVisual,
    PlacedPart,
    clamp_of,
    default_fork,
    get_unit_type,
    mounting_of,
    new_component_params,
    set_param,
)
from olayout.core.geometry import Pose
from olayout.core.layout import Component
from olayout.engine.world import BuildCache
from olayout.optics.elements import OpticKind
from olayout.ui.canvas.sprite_store import SpriteStore

SHADOW_PER_INCH = 0.045  # shadow offset per inch of height above the table
LAYER_HEIGHT = {Layer.BASE: 0.0, Layer.OPTIC: 2.4}  # nominal heights for shadow casting


# ------------------------------------------------------------------ paths
def polygon_path(geometry) -> QPainterPath:
    """shapely (multi)polygon in the model frame -> QPainterPath (y flipped)."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for poly in getattr(geometry, "geoms", [geometry]):
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        for ring in [poly.exterior, *poly.interiors]:
            pts = [QPointF(x, -y) for x, y in ring.coords]
            if len(pts) >= 3:
                path.addPolygon(QPolygonF(pts))
                path.closeSubpath()
    return path


def apply_pose(painter: QPainter, pose: Pose) -> None:
    painter.translate(pose.x, -pose.y)
    painter.rotate(-pose.angle)


# ------------------------------------------------------------------ parts
def paint_parts(
    painter: QPainter,
    parts: list[PlacedPart],
    shadow_vector: QPointF | None,
    layer_height: float,
) -> None:
    """Draw part sprites in height order, each with its soft drop shadow.

    ``shadow_vector`` is the shadow offset per inch of height, expressed in
    the item's coordinates (so shadows always fall the same way on screen).
    """
    store = SpriteStore.instance()
    for part in sorted(parts, key=lambda p: p.top):
        sprite = store.get(part)
        transform = part_transform(part)
        if shadow_vector is not None:
            height = max(layer_height + sprite.z_top, 0.25)
            painter.save()
            painter.translate(shadow_vector * height)
            painter.setTransform(transform, True)
            painter.drawPixmap(sprite.rect, sprite.shadow, QRectF(sprite.shadow.rect()))
            painter.restore()
        painter.save()
        painter.setTransform(transform, True)
        painter.drawPixmap(sprite.rect, sprite.color, QRectF(sprite.color.rect()))
        painter.restore()


def part_transform(part: PlacedPart) -> QTransform:
    """Part frame (y down) -> item frame, matching :func:`apply_pose`."""
    return QTransform().translate(part.pose.x, -part.pose.y).rotate(-part.pose.angle)


def parts_bounds(parts: list[PlacedPart]) -> QRectF:
    """Bounding rect of part sprites in item coordinates."""
    store = SpriteStore.instance()
    rect = QRectF()
    for part in parts:
        mapped = part_transform(part).mapRect(store.get(part).rect)
        rect = mapped if rect.isNull() else rect.united(mapped)
    return rect


# ----------------------------------------------------------------- optics
MIRROR_SURFACE = QColor("#eef3f9")
MIRROR_BODY = QColor("#4b535e")
GLASS_FILL = QColor(150, 210, 255, 105)
GLASS_EDGE = QColor(200, 235, 255, 220)
SPLITTER = QColor(205, 175, 255, 230)
BLOCK = QColor("#2a2c30")


def paint_optic(painter: QPainter, visual: OpticVisual) -> None:
    """Schematic top view of the optic held in a mount (drawn over the sprite)."""
    painter.save()
    apply_pose(painter, visual.pose)
    half = visual.diameter / 2
    t = visual.thickness
    kind = visual.kind
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if kind is OpticKind.MIRROR:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(MIRROR_BODY)
        painter.drawRect(QRectF(-t, -half, t, 2 * half))
        pen = QPen(MIRROR_SURFACE, 0.045)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        if visual.power_sign:
            sag = 0.06 * visual.power_sign
            path = QPainterPath(QPointF(0, -half))
            path.quadTo(QPointF(-2 * sag, 0), QPointF(0, half))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        else:
            painter.drawLine(QPointF(-0.0225, -half), QPointF(-0.0225, half))
    elif kind is OpticKind.LENS:
        xc = -t / 2
        edge = 0.05
        bulge = (t - edge) if visual.power_sign >= 0 else -(t - edge) * 0.8
        path = QPainterPath(QPointF(xc - edge / 2, -half))
        path.quadTo(QPointF(xc - edge / 2 - bulge, 0), QPointF(xc - edge / 2, half))
        path.lineTo(QPointF(xc + edge / 2, half))
        path.quadTo(QPointF(xc + edge / 2 + bulge, 0), QPointF(xc + edge / 2, -half))
        path.closeSubpath()
        painter.setBrush(GLASS_FILL)
        painter.setPen(QPen(GLASS_EDGE, 0.012))
        painter.drawPath(path)
    elif kind is OpticKind.SPLITTER:
        pen = QPen(SPLITTER, 0.03)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(-0.015, -half), QPointF(-0.015, half))
    elif kind is OpticKind.WINDOW:
        painter.setBrush(GLASS_FILL)
        painter.setPen(QPen(GLASS_EDGE, 0.01))
        painter.drawRect(QRectF(-t, -half, t, 2 * half))
    elif kind is OpticKind.BLOCK:
        painter.setBrush(BLOCK)
        painter.setPen(QPen(QColor("#6b6f76"), 0.01))
        painter.drawRect(QRectF(-t, -half, t, 2 * half))
    painter.restore()


def paint_source_icon(painter: QPainter, color: QColor, enabled: bool = True) -> None:
    """A compact laser-head glyph at the unit origin, emitting toward +x."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    body = QRectF(-0.70, -0.20, 0.62, 0.40)
    grad = QLinearGradient(0, -0.2, 0, 0.2)
    grad.setColorAt(0.0, QColor("#4b5058"))
    grad.setColorAt(0.5, QColor("#2c3036"))
    grad.setColorAt(1.0, QColor("#1d2024"))
    painter.setPen(QPen(QColor("#6d737c"), 0.015))
    painter.setBrush(QBrush(grad))
    painter.drawRoundedRect(body, 0.06, 0.06)
    painter.setBrush(QColor("#9aa1aa"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRect(QRectF(-0.08, -0.09, 0.08, 0.18))  # aperture
    glow = QColor(color)
    glow.setAlpha(230 if enabled else 70)
    painter.setBrush(glow)
    painter.drawEllipse(QPointF(-0.43, 0.0), 0.07, 0.07)  # emission LED
    painter.restore()


def source_icon_rect() -> QRectF:
    return QRectF(-0.74, -0.24, 0.78, 0.48)


# ------------------------------------------------------------- thumbnails
FORK_PART_IDS = frozenset({*P.CLAMPS, "SCREW"})  # the pedestal's swivelling clamp
HOLDER_PART_IDS = FORK_PART_IDS | {"PH2", "PH2@BE1", "BE1"}  # what a mount stands on
THUMB_FORK_OPACITY = 0.4


def thumbnail_framing(parts: list[PlacedPart]) -> list[PlacedPart]:
    """The parts a thumbnail is framed on: the component itself, not the post
    holder or pedestal it stands on, so a pedestal unit is drawn exactly like
    its hole-mounted twin.  A bare base has nothing else: it is framed whole."""
    return [p for p in parts if p.part_id not in HOLDER_PART_IDS] or parts


def unit_thumbnail(type_id: str, size: int = 64, angle: float = 90.0, params: dict | None = None) -> QPixmap:
    """Render a unit as it appears on the board (optic facing up), for the library.

    The post holder or pedestal shows underneath, the pedestal's clamping fork
    only faintly; either may run off the edge.  ``params``: e.g. a library
    preset (a clamped post); defaults otherwise.
    """
    unit = get_unit_type(type_id)
    params = new_component_params(type_id) if params is None else params
    for key, value in unit.preview_params:  # e.g. a short rail, so it does not shrink to a line
        set_param(params, key, value)
    comp = Component(id="thumb", type=type_id, angle=angle, params=params)
    fork = default_fork(clamp_of(comp)) if mounting_of(comp) is Mounting.PEDESTAL else None
    placed = BuildCache().place(comp, fork)
    parts = list(placed.parts)
    framed = thumbnail_framing(parts)

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    bounds = parts_bounds(framed) if framed else source_icon_rect()
    rotated = QTransform().rotate(-angle).mapRect(bounds)
    scale = 0.88 * size / max(rotated.width(), rotated.height(), 1e-6)
    painter.translate(size / 2, size / 2)
    painter.scale(scale, scale)
    painter.translate(-rotated.center())
    painter.rotate(-angle)
    if parts:
        clamp = [] if framed is parts else [p for p in parts if p.part_id in FORK_PART_IDS]
        if clamp:
            painter.setOpacity(THUMB_FORK_OPACITY)
            paint_parts(painter, clamp, QPointF(0.02, 0.02), 0.0)
            painter.setOpacity(1.0)
        for layer in Layer:
            layer_parts = [p for p in parts if p.layer is layer and not any(p is c for c in clamp)]
            paint_parts(painter, layer_parts, QPointF(0.02, 0.02), LAYER_HEIGHT[layer] * 0.4)
        for visual in placed.build.optic_visuals:
            paint_optic(painter, visual)
    else:
        paint_source_icon(painter, QColor("#ff5a5a"))
    painter.end()
    return pixmap
