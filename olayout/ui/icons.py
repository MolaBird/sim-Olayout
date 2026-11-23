"""Toolbar icons drawn with QPainter (crisp at any DPI, no asset files)."""

from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from olayout.ui.theme import C

SIZE = 64


def _canvas() -> tuple[QPixmap, QPainter]:
    pix = QPixmap(SIZE, SIZE)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(C.TEXT, 4.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    return pix, p


def _arrow_head(p: QPainter, tip: QPointF, angle_deg: float, size: float = 9.0) -> None:
    a = math.radians(angle_deg)
    left = tip - QPointF(size * math.cos(a - 0.5), size * math.sin(a - 0.5))
    right = tip - QPointF(size * math.cos(a + 0.5), size * math.sin(a + 0.5))
    p.drawPolyline(QPolygonF([left, tip, right]))


def _draw(name: str, p: QPainter) -> None:
    if name == "select":
        path = QPainterPath(QPointF(18, 10))
        for x, y in ((18, 50), (28, 40), (35, 55), (42, 52), (35, 37), (49, 37)):
            path.lineTo(x, y)
        path.closeSubpath()
        p.setBrush(C.TEXT)
        p.drawPath(path)
    elif name == "measure":
        p.save()
        p.translate(32, 32)
        p.rotate(-35)
        p.drawRoundedRect(QRectF(-26, -9, 52, 18), 3, 3)
        for k, x in enumerate(range(-18, 22, 6)):
            p.drawLine(QPointF(x, -9), QPointF(x, -3 if k % 2 else 1))
        p.restore()
    elif name == "probe":
        p.drawEllipse(QPointF(32, 32), 13, 13)
        for a, b in (((32, 6), (32, 17)), ((32, 47), (32, 58)), ((6, 32), (17, 32)), ((47, 32), (58, 32))):
            p.drawLine(QPointF(*a), QPointF(*b))
        p.setBrush(C.PROBE)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(32, 32), 4, 4)
    elif name == "new":
        p.drawPolyline(QPolygonF([QPointF(40, 8), QPointF(16, 8), QPointF(16, 56), QPointF(48, 56), QPointF(48, 16), QPointF(40, 8), QPointF(40, 16), QPointF(48, 16)]))
    elif name == "open":
        p.drawPolyline(QPolygonF([QPointF(8, 50), QPointF(8, 14), QPointF(24, 14), QPointF(29, 20), QPointF(52, 20), QPointF(52, 28)]))
        p.drawPolygon(QPolygonF([QPointF(8, 50), QPointF(16, 28), QPointF(58, 28), QPointF(50, 50)]))
    elif name == "save":
        p.drawRoundedRect(QRectF(10, 10, 44, 44), 5, 5)
        p.drawRect(QRectF(20, 10, 22, 13))
        p.drawRect(QRectF(19, 33, 26, 21))
    elif name in ("undo", "redo"):
        p.save()
        if name == "redo":
            p.translate(64, 0)
            p.scale(-1, 1)
        path = QPainterPath(QPointF(16, 28))
        path.cubicTo(QPointF(26, 14), QPointF(52, 16), QPointF(50, 38))
        path.cubicTo(QPointF(48, 50), QPointF(38, 52), QPointF(30, 52))
        p.drawPath(path)
        _arrow_head(p, QPointF(14, 28), 200, 11)
        p.restore()
    elif name == "rotate":
        p.drawArc(QRectF(14, 14, 36, 36), 60 * 16, 290 * 16)
        _arrow_head(p, QPointF(42, 17), -20, 11)
    elif name == "flip":
        p.drawRect(QRectF(12, 18, 16, 28))
        p.setPen(QPen(C.TEXT, 4.2, Qt.PenStyle.DashLine))
        p.drawRect(QRectF(36, 18, 16, 28))
        p.setPen(QPen(C.ACCENT, 3))
        p.drawLine(QPointF(32, 8), QPointF(32, 56))
    elif name == "fit":
        for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            cx, cy = 32 + 20 * sx, 32 + 20 * sy
            p.drawLine(QPointF(cx, cy), QPointF(cx - 10 * sx, cy))
            p.drawLine(QPointF(cx, cy), QPointF(cx, cy - 10 * sy))
        p.drawRect(QRectF(22, 24, 20, 16))
    elif name == "snap":
        path = QPainterPath(QPointF(14, 12))
        path.lineTo(14, 34)
        path.arcTo(QRectF(14, 18, 36, 36), 180, 180)
        path.lineTo(50, 12)
        p.drawPath(path)
        p.setPen(QPen(C.ERROR, 6))
        p.drawLine(QPointF(14, 14), QPointF(14, 22))
        p.drawLine(QPointF(50, 14), QPointF(50, 22))
    elif name == "beam":
        p.setPen(QPen(QColor("#ff5a5a"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPolyline(QPolygonF([QPointF(6, 46), QPointF(30, 46), QPointF(30, 14), QPointF(58, 14)]))
        p.setPen(QPen(C.TEXT, 4))
        p.drawLine(QPointF(22, 54), QPointF(38, 38))
        p.drawLine(QPointF(22, 22), QPointF(38, 6))
    elif name == "layers":
        for dy, alpha in ((14, 255), (0, 150)):
            poly = QPolygonF([QPointF(32, 18 + dy), QPointF(56, 30 + dy), QPointF(32, 42 + dy), QPointF(8, 30 + dy)])
            color = QColor(C.TEXT)
            color.setAlpha(alpha)
            p.setPen(QPen(color, 4))
            p.drawPolygon(poly)
    elif name == "labels":
        p.drawRoundedRect(QRectF(8, 18, 48, 28), 6, 6)
        p.drawLine(QPointF(18, 32), QPointF(46, 32))
    elif name == "delete":
        p.drawLine(QPointF(14, 16), QPointF(50, 16))
        p.drawPolyline(QPolygonF([QPointF(18, 16), QPointF(21, 56), QPointF(43, 56), QPointF(46, 16)]))
        p.drawLine(QPointF(26, 10), QPointF(38, 10))
    elif name == "duplicate":
        p.drawRoundedRect(QRectF(10, 10, 30, 30), 4, 4)
        p.drawRoundedRect(QRectF(24, 24, 30, 30), 4, 4)
    elif name == "export":
        p.drawRoundedRect(QRectF(10, 14, 44, 40), 5, 5)
        p.drawLine(QPointF(32, 6), QPointF(32, 36))
        _arrow_head(p, QPointF(32, 6), -90, 10)
    elif name == "ghost":
        p.drawEllipse(QPointF(26, 32), 15, 15)
        p.setPen(QPen(C.TEXT, 4, Qt.PenStyle.DotLine))
        p.drawEllipse(QPointF(38, 32), 15, 15)


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    pix, p = _canvas()
    _draw(name, p)
    p.end()
    return QIcon(pix)
