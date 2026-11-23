"""QGraphicsItems drawn on the layout canvas.

Scene units are inches with Qt's y axis pointing down; the model's y axis
points up, so a model point (x, y) sits at scene (x, -y).
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from olayout.catalog.parts import Layer
from olayout.core.layout import Measurement
from olayout.core.units import format_fraction, to_mm
from olayout.engine.world import PlacedUnit
from olayout.optics.tracer import BeamSegment, TraceResult
from olayout.ui.canvas.painting import (
    LAYER_HEIGHT,
    SHADOW_PER_INCH,
    paint_optic,
    paint_parts,
    paint_source_icon,
    parts_bounds,
    polygon_path,
    source_icon_rect,
)
from olayout.ui.theme import C, wavelength_color

Z_BASE = 10
Z_OPTIC = 20
Z_ISSUES = 25
Z_BEAMS = 30
Z_MEASURE = 40
Z_PROBE = 41
Z_SELECTION = 50
Z_GHOST = 55
Z_OVERLAY = 60
Z_LABEL = 70


def to_scene(x: float, y: float) -> QPointF:
    return QPointF(x, -y)


def cosmetic_pen(color: QColor, width: float = 1.5, style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def view_scale(painter: QPainter) -> float:
    """Pixels per scene unit (inch) for the current painter."""
    t = painter.worldTransform()
    return math.hypot(t.m11(), t.m12()) or 1.0


# ==========================================================================
# Components
# ==========================================================================
class ComponentLayerItem(QGraphicsItem):
    """One layer (base or optic) of one placed component.

    Each component gets one item per layer so that *all* bases render below
    *all* optics, matching the physical stacking on the table.
    """

    def __init__(self, comp_id: str, layer: Layer, ghost: bool = False):
        super().__init__()
        self.comp_id = comp_id
        self.layer = layer
        self.ghost = ghost
        self.setZValue((Z_GHOST if ghost else 0) + (Z_BASE if layer is Layer.BASE else Z_OPTIC))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._parts = []
        self._visuals = []
        self._shape = QPainterPath()
        self._bounds = QRectF()
        self._source_color: QColor | None = None
        self._source_enabled = True
        self.geometry_key: tuple | None = None
        self.selected = False
        self.hovered = False
        self.illegal = False
        self.opacity_override = 1.0
        if ghost:
            self.setOpacity(0.62)

    # ---------------------------------------------------------- geometry
    def set_geometry(self, placed: PlacedUnit, source_color: QColor | None = None) -> None:
        parts = [p for p in placed.parts if p.layer is self.layer]
        visuals = list(placed.build.optic_visuals) if self.layer is Layer.OPTIC else []
        is_source = self.layer is Layer.OPTIC and placed.is_virtual
        self.prepareGeometryChange()
        self._parts = parts
        self._visuals = visuals
        self._source_color = (source_color or QColor("#ff5a5a")) if is_source else None
        self._source_enabled = bool(placed.build.emitters)
        local = placed.local_footprints[self.layer]
        if self._source_color is not None:
            path = QPainterPath()
            path.addRoundedRect(source_icon_rect(), 0.06, 0.06)
            self._shape = path
            bounds = source_icon_rect()
        else:
            self._shape = polygon_path(local)
            bounds = parts_bounds(parts) if parts else QRectF()
            bounds = bounds.united(self._shape.boundingRect()) if not bounds.isNull() else self._shape.boundingRect()
        margin = 0.35  # shadows and outlines
        self._bounds = bounds.adjusted(-margin, -margin, margin, margin)
        self.update()

    def has_content(self) -> bool:
        return bool(self._parts) or self._source_color is not None

    def set_pose(self, x: float, y: float, angle: float) -> None:
        self.setPos(x, -y)
        self.setRotation(-angle)

    def footprint_path(self) -> QPainterPath:
        return self._shape

    # ----------------------------------------------------------- QGraphicsItem
    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def shape(self) -> QPainterPath:
        return self._shape

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.opacity_override != 1.0:
            painter.setOpacity(painter.opacity() * self.opacity_override)
        # Shadows fall toward the lower right of the screen regardless of rotation.
        shadow = QTransform().rotate(-self.rotation()).map(QPointF(SHADOW_PER_INCH, SHADOW_PER_INCH))
        if self._parts:
            paint_parts(painter, self._parts, shadow, LAYER_HEIGHT[self.layer])
        for visual in self._visuals:
            paint_optic(painter, visual)
        if self._source_color is not None:
            paint_source_icon(painter, self._source_color, self._source_enabled)
        painter.setOpacity(1.0)

        if self.ghost:
            return
        if self.illegal:
            painter.setBrush(C.ERROR_FILL)
            painter.setPen(cosmetic_pen(C.ERROR, 1.6))
            painter.drawPath(self._shape)
        if self.selected:
            style = Qt.PenStyle.SolidLine if self.layer is Layer.OPTIC else Qt.PenStyle.DashLine
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(cosmetic_pen(C.ACCENT, 1.6, style))
            painter.drawPath(self._shape)
        elif self.hovered:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(cosmetic_pen(QColor(255, 255, 255, 120), 1.2))
            painter.drawPath(self._shape)


# ==========================================================================
# Beams
# ==========================================================================
class BeamItem(QGraphicsItem):
    """All traced beams, drawn as true-scale Gaussian envelopes (1/e² radius)."""

    SAMPLES = 40

    def __init__(self):
        super().__init__()
        self.setZValue(Z_BEAMS)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._items: list[tuple[QPolygonF, QLineF, QColor, float, str, list[QPointF]]] = []
        self._bounds = QRectF()
        self.exaggeration = 1.0
        self.highlight_source: str | None = None

    def set_trace(self, trace: TraceResult, exaggeration: float | None = None) -> None:
        if exaggeration is not None:
            self.exaggeration = exaggeration
        self.prepareGeometryChange()
        self._items = []
        bounds = QRectF()
        power0: dict[str, float] = {}
        for seg in trace.segments:
            if seg.parent is None:
                power0[seg.source] = max(seg.state.power, 1e-12)
            polygon, waists = self._envelope(seg)
            line = QLineF(to_scene(*seg.start), to_scene(*seg.end))
            color = wavelength_color(seg.state.wavelength * 25.4e6)
            rel = seg.state.power / power0.get(seg.source, max(seg.state.power, 1e-12))
            strength = float(np.clip(0.35 + 0.65 * rel**0.35, 0.2, 1.0))
            self._items.append((polygon, line, color, strength, seg.end_event, waists))
            bounds = bounds.united(polygon.boundingRect()) if not bounds.isNull() else polygon.boundingRect()
        self._bounds = bounds.adjusted(-0.5, -0.5, 0.5, 0.5)
        self.update()

    def _envelope(self, seg: BeamSegment) -> tuple[QPolygonF, list[QPointF]]:
        q = seg.state.q_t
        z_r = q.imag
        s_waist = -q.real
        w0 = seg.state.tangential.waist_radius
        length = max(seg.length, 1e-9)
        # Sample densely near the waist: s = s_w + zR*sinh(u), u uniform.
        u0 = math.asinh((0.0 - s_waist) / z_r)
        u1 = math.asinh((length - s_waist) / z_r)
        u = np.linspace(u0, u1, self.SAMPLES)
        s = np.clip(s_waist + z_r * np.sinh(u), 0.0, length)
        s[0], s[-1] = 0.0, length
        w = w0 * np.sqrt(1.0 + ((s - s_waist) / z_r) ** 2) * self.exaggeration
        dx, dy = seg.direction
        nx, ny = -dy, dx
        px = seg.start[0] + s * dx
        py = seg.start[1] + s * dy
        left = [QPointF(x + nx * r, -(y + ny * r)) for x, y, r in zip(px, py, w)]
        right = [QPointF(x - nx * r, -(y - ny * r)) for x, y, r in zip(px[::-1], py[::-1], w[::-1])]
        waists = [to_scene(*seg.point_at(sw)) for sw in seg.waist_positions()]
        return QPolygonF(left + right), waists

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scale = view_scale(painter)
        for polygon, line, color, strength, event, waists in self._items:
            fill = QColor(color)
            fill.setAlphaF(0.30 * strength)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPolygon(polygon)
            glow = QColor(color)
            glow.setAlphaF(0.22 * strength)
            painter.setPen(cosmetic_pen(glow, 4.0))
            painter.drawLine(line)
            core = QColor(color)
            core.setAlphaF(0.95 * strength)
            painter.setPen(cosmetic_pen(core, 1.4))
            painter.drawLine(line)
            if scale > 25:
                tick = 5.0 / scale
                painter.setPen(cosmetic_pen(core, 1.2))
                normal = QPointF(-line.dy(), line.dx())
                norm = math.hypot(normal.x(), normal.y()) or 1.0
                normal = normal / norm * tick
                for waist in waists:
                    painter.drawLine(waist - normal, waist + normal)
            if event == "blocked":
                end = line.p2()
                r = 4.0 / scale
                painter.setPen(cosmetic_pen(QColor(255, 255, 255, 200), 1.6))
                painter.drawLine(end + QPointF(-r, -r), end + QPointF(r, r))
                painter.drawLine(end + QPointF(-r, r), end + QPointF(r, -r))


# ==========================================================================
# Issues
# ==========================================================================
class IssueOverlayItem(QGraphicsItem):
    """Hatched red regions where parts overlap."""

    def __init__(self):
        super().__init__()
        self.setZValue(Z_ISSUES)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._paths: list[QPainterPath] = []
        self._bounds = QRectF()

    def set_regions(self, regions) -> None:
        self.prepareGeometryChange()
        self._paths = [polygon_path(r) for r in regions if r is not None and not r.is_empty]
        bounds = QRectF()
        for path in self._paths:
            bounds = bounds.united(path.boundingRect())
        self._bounds = bounds.adjusted(-0.1, -0.1, 0.1, 0.1)
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if not self._paths:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(cosmetic_pen(QColor(255, 235, 238), 1.2))
        painter.setBrush(QColor(255, 45, 70, 170))
        for path in self._paths:
            painter.drawPath(path)


# ==========================================================================
# Labels (constant screen size)
# ==========================================================================
class LabelItem(QGraphicsItem):
    """A small pill-shaped text label that ignores zoom."""

    def __init__(self, text: str = "", color: QColor = C.TEXT, background: QColor | None = None, parent=None):
        super().__init__(parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(Z_LABEL)
        self._font = QFont()
        self._font.setPointSizeF(8.5)
        self._color = color
        self._background = background if background is not None else QColor(20, 22, 26, 215)
        self._lines: list[str] = []
        self._rect = QRectF()
        self.anchor = QPointF(10, 10)  # offset of the box from the item's position, in pixels
        self.set_text(text)

    def set_text(self, text: str) -> None:
        lines = text.split("\n") if text else []
        if lines == self._lines:
            return
        self.prepareGeometryChange()
        self._lines = lines
        fm = QFontMetricsF(self._font)
        width = max((fm.horizontalAdvance(line) for line in lines), default=0.0)
        height = fm.height() * len(lines)
        self._rect = QRectF(0, 0, width + 12, height + 6) if lines else QRectF()
        self.update()

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._rect.translated(self.anchor)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if not self._lines:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self._rect.translated(self.anchor)
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.setBrush(self._background)
        painter.drawRoundedRect(rect, 5, 5)
        painter.setFont(self._font)
        painter.setPen(self._color)
        fm = QFontMetricsF(self._font)
        y = rect.top() + 3 + fm.ascent()
        for line in self._lines:
            painter.drawText(QPointF(rect.left() + 6, y), line)
            y += fm.height()


# ==========================================================================
# Measurements
# ==========================================================================
def measurement_text(a: tuple[float, float], b: tuple[float, float]) -> str:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    angle = math.degrees(math.atan2(dy, dx))
    return f'{length:.3f}"  {format_fraction(length)}  ({to_mm(length):.2f} mm)\nΔx {dx:+.3f}"  Δy {dy:+.3f}"  ∠ {angle:.1f}°'


class MeasurementItem(QGraphicsItem):
    def __init__(self, measurement: Measurement | None = None, preview: bool = False):
        super().__init__()
        self.setZValue(Z_MEASURE)
        self.ann_id = measurement.id if measurement else ""
        self.preview = preview
        self.selected = False
        self.a = QPointF()
        self.b = QPointF()
        self.label = LabelItem(color=C.MEASURE, parent=self)
        if measurement is not None:
            self.set_points(measurement.a, measurement.b)

    def set_points(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        self.prepareGeometryChange()
        self.a = to_scene(*a)
        self.b = to_scene(*b)
        self.label.setPos((self.a + self.b) / 2)
        self.label.set_text(measurement_text(a, b))
        self.update()

    def endpoints_model(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return (self.a.x(), -self.a.y()), (self.b.x(), -self.b.y())

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(self.a, self.b).normalized().adjusted(-0.4, -0.4, 0.4, 0.4)

    def shape(self) -> QPainterPath:
        path = QPainterPath(self.a)
        path.lineTo(self.b)
        stroker = QPainterPathStroker()
        stroker.setWidth(0.12)
        return stroker.createStroke(path)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor(C.MEASURE)
        if self.selected:
            painter.setPen(cosmetic_pen(QColor(255, 255, 255, 90), 6.0))
            painter.drawLine(self.a, self.b)
        painter.setPen(cosmetic_pen(color, 1.5, Qt.PenStyle.DashLine if self.preview else Qt.PenStyle.SolidLine))
        painter.drawLine(self.a, self.b)
        scale = view_scale(painter)
        tick = 6.0 / scale
        line = QLineF(self.a, self.b)
        if line.length() > 1e-9:
            n = QPointF(-line.dy(), line.dx()) / line.length() * tick
            painter.setPen(cosmetic_pen(color, 1.5))
            for p in (self.a, self.b):
                painter.drawLine(p - n, p + n)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        r = 2.5 / scale
        painter.drawEllipse(self.a, r, r)
        painter.drawEllipse(self.b, r, r)


# ==========================================================================
# Probes, snapping, handles
# ==========================================================================
class ProbeItem(QGraphicsItem):
    def __init__(self, ann_id: str):
        super().__init__()
        self.ann_id = ann_id
        self.setZValue(Z_PROBE)
        self.selected = False
        self.active = True
        self.label = LabelItem(color=C.PROBE, parent=self)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-0.25, -0.25, 0.5, 0.5)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), 0.12, 0.12)
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scale = view_scale(painter)
        r = 5.0 / scale
        color = C.PROBE if self.active else C.TEXT_DIM
        if self.selected:
            painter.setPen(cosmetic_pen(QColor(255, 255, 255, 110), 5.0))
            painter.drawEllipse(QPointF(0, 0), r, r)
        painter.setPen(cosmetic_pen(color, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(0, 0), r, r)
        painter.drawLine(QPointF(-2 * r, 0), QPointF(-r, 0))
        painter.drawLine(QPointF(r, 0), QPointF(2 * r, 0))
        painter.drawLine(QPointF(0, -2 * r), QPointF(0, -r))
        painter.drawLine(QPointF(0, r), QPointF(0, 2 * r))


class SnapIndicatorItem(QGraphicsItem):
    """A ring marking the hole / beam point a drag snaps to."""

    def __init__(self):
        super().__init__()
        self.setZValue(Z_OVERLAY)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.color = C.ACCENT
        self.setVisible(False)

    def show_at(self, x: float, y: float, color: QColor = C.ACCENT) -> None:
        self.color = color
        self.setPos(to_scene(x, y))
        self.setVisible(True)
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-0.3, -0.3, 0.6, 0.6)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = 7.0 / view_scale(painter)
        painter.setPen(cosmetic_pen(self.color, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(0, 0), r, r)
        painter.drawEllipse(QPointF(0, 0), r * 0.25, r * 0.25)


HANDLE_PX = 70.0  # rotation handle distance from the post axis, in pixels
HANDLE_MIN_IN = 1.3


def handle_distance(scale: float) -> float:
    """Rotation-handle distance (inches) for the current zoom."""
    return max(HANDLE_PX / max(scale, 1e-6), HANDLE_MIN_IN)


class SelectionOverlayItem(QGraphicsItem):
    """Post-axis marker and rotation handle for a single selected component."""

    def __init__(self):
        super().__init__()
        self.setZValue(Z_SELECTION)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.active = False
        self.post = QPointF()
        self.angle = 0.0
        self.optic_center: QPointF | None = None
        self.hot = False

    def set_target(self, x: float, y: float, angle: float, optic_center: tuple[float, float] | None) -> None:
        self.prepareGeometryChange()
        self.active = True
        self.post = to_scene(x, y)
        self.angle = angle
        self.optic_center = to_scene(*optic_center) if optic_center else None
        self.update()

    def clear(self) -> None:
        self.prepareGeometryChange()
        self.active = False
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        if not self.active:
            return QRectF()
        r = 12.0  # generous: handle distance depends on zoom
        return QRectF(self.post.x() - r, self.post.y() - r, 2 * r, 2 * r)

    def handle_pos(self, scale: float) -> QPointF:
        d = handle_distance(scale)
        rad = math.radians(self.angle)
        return self.post + QPointF(d * math.cos(rad), -d * math.sin(rad))

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if not self.active:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scale = view_scale(painter)
        handle = self.handle_pos(scale)
        color = QColor(C.ACCENT)
        painter.setPen(cosmetic_pen(QColor(74, 168, 255, 150), 1.2, Qt.PenStyle.DashLine))
        painter.drawLine(self.post, handle)
        r = (7.0 if self.hot else 5.5) / scale
        painter.setPen(cosmetic_pen(color, 1.6))
        painter.setBrush(QColor(20, 22, 26, 230))
        painter.drawEllipse(handle, r, r)
        # Post axis cross.
        c = 4.0 / scale
        painter.setPen(cosmetic_pen(color, 1.3))
        painter.drawLine(self.post + QPointF(-c, 0), self.post + QPointF(c, 0))
        painter.drawLine(self.post + QPointF(0, -c), self.post + QPointF(0, c))
        if self.optic_center is not None:
            painter.setPen(cosmetic_pen(QColor(255, 255, 255, 200), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.optic_center, 3.0 / scale, 3.0 / scale)
