"""The layout scene: breadboard background + items mirroring the document."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QGraphicsScene

from olayout.catalog.parts import Layer
from olayout.catalog.units import get_param
from olayout.core.units import format_beam_size, format_optical_distance
from olayout.ui.canvas.items import (
    BeamItem,
    ComponentLayerItem,
    IssueOverlayItem,
    LabelItem,
    MeasurementItem,
    ProbeItem,
    SelectionOverlayItem,
    SnapIndicatorItem,
    Z_LABEL,
    cosmetic_pen,
    to_scene,
    view_scale,
)
from olayout.ui.document import LayoutDocument
from olayout.ui.theme import C, wavelength_color

SCENE_MARGIN = 1000.0  # inches of canvas around the board: effectively unbounded, so zoom/pan never hit an edge
BOARD_CORNER_R = 0.13  # MB breadboard corner radius


def _beam_color_for(comp) -> QColor:
    nm = get_param(comp.params, "wavelength_nm", None) or get_param(comp.params, "source.wavelength_nm", 633.0)
    try:
        return wavelength_color(float(nm))
    except (TypeError, ValueError):
        return wavelength_color(633.0)


class LayoutScene(QGraphicsScene):
    def __init__(self, doc: LayoutDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)  # items move constantly
        self.comp_items: dict[str, dict[Layer, ComponentLayerItem]] = {}
        self.comp_labels: dict[str, LabelItem] = {}
        self.measure_items: dict[str, MeasurementItem] = {}
        self.probe_items: dict[str, ProbeItem] = {}
        self.selected_annotations: set[str] = set()
        self.hovered_id: str | None = None

        self.beam_item = BeamItem()
        self.issue_item = IssueOverlayItem()
        self.selection_overlay = SelectionOverlayItem()
        self.snap_indicator = SnapIndicatorItem()
        self.hover_label = LabelItem(color=C.TEXT)
        self.hover_label.setZValue(Z_LABEL + 5)
        self.hover_label.setVisible(False)
        for item in (self.beam_item, self.issue_item, self.selection_overlay, self.snap_indicator, self.hover_label):
            self.addItem(item)

        # View options
        self.show_beams = True
        self.show_labels = True
        self.show_bases = True
        self.show_optics = True
        self.ghost_optics = False  # semi-transparent optic layer to inspect bases
        self.beam_exaggeration = 1.0
        self._hole_pixmaps: dict[tuple[int, int], QPixmap] = {}

        doc.layoutReset.connect(self.sync_all)
        doc.componentsChanged.connect(self.update_component_poses)
        doc.analysisUpdated.connect(self.apply_analysis)
        doc.selectionChanged.connect(self.apply_selection)
        doc.annotationsChanged.connect(self.sync_annotations)
        self.sync_all()

    # ================================================================ board
    def board_rect(self) -> QRectF:
        b = self.doc.layout.breadboard
        return QRectF(0.0, -b.height, b.width, b.height)

    def update_scene_rect(self) -> None:
        self.setSceneRect(self.board_rect().adjusted(-SCENE_MARGIN, -SCENE_MARGIN, SCENE_MARGIN, SCENE_MARGIN))

    def _hole_pixmap(self, diameter_px: float, dpr: float) -> QPixmap:
        key = (round(diameter_px * 4), round(dpr * 100))
        pix = self._hole_pixmaps.get(key)
        if pix is None:
            outer = diameter_px * 1.45  # the countersink is wider than the thread
            size = max(2, math.ceil(outer + 2))
            pix = QPixmap(round(size * dpr), round(size * dpr))
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            c = QPointF(size / 2, size / 2)
            rim = QRadialGradient(c, outer / 2)
            rim.setColorAt(0.0, QColor(30, 32, 36))
            rim.setColorAt(0.62, QColor(52, 55, 60))
            rim.setColorAt(0.80, QColor(150, 155, 162))
            rim.setColorAt(1.0, QColor(150, 155, 162, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rim)
            p.drawEllipse(c, outer / 2, outer / 2)
            p.setBrush(QColor(22, 23, 26))
            p.drawEllipse(c, diameter_px / 2, diameter_px / 2)
            p.end()
            if len(self._hole_pixmaps) > 64:
                self._hole_pixmaps.clear()
            self._hole_pixmaps[key] = pix
        return pix

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        painter.fillRect(rect, C.CANVAS)
        board = self.doc.layout.breadboard
        board_rect = self.board_rect()
        scale = view_scale(painter)

        corner = BOARD_CORNER_R
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 110))
        painter.drawRoundedRect(board_rect.translated(0.10, 0.12), corner, corner)
        grad = QLinearGradient(board_rect.topLeft(), board_rect.bottomRight())
        grad.setColorAt(0.0, C.BOARD.lighter(108))
        grad.setColorAt(1.0, C.BOARD.darker(108))
        painter.setBrush(grad)
        painter.drawRoundedRect(board_rect, corner, corner)
        painter.setPen(cosmetic_pen(C.BOARD_EDGE, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(board_rect, corner, corner)

        # Counterbored mounting holes (for bolting the board down; not usable for posts).
        painter.setPen(cosmetic_pen(QColor(70, 74, 80), 1.0))
        for x, y in board.mounting_counterbores():
            painter.setBrush(QColor(58, 61, 66))
            painter.drawEllipse(QPointF(x, -y), 0.22, 0.22)
            painter.setBrush(QColor(26, 27, 30))
            painter.drawEllipse(QPointF(x, -y), 0.14, 0.14)

        pitch_px = board.pitch * scale
        if pitch_px < 5 or board.columns == 0 or board.rows == 0:
            return
        visible = rect.intersected(board_rect)
        if visible.isEmpty():
            return
        pad = board.hole_diameter
        c0 = max(0, math.floor((visible.left() - pad - board.margin) / board.pitch))
        c1 = min(board.columns - 1, math.ceil((visible.right() + pad - board.margin) / board.pitch))
        r0 = max(0, math.floor((-visible.bottom() - pad - board.margin) / board.pitch))
        r1 = min(board.rows - 1, math.ceil((-visible.top() + pad - board.margin) / board.pitch))
        dpr = painter.device().devicePixelRatioF() if painter.device() else 1.0
        pix = self._hole_pixmap(board.hole_diameter * scale, dpr)
        half = QPointF(pix.width() / dpr / 2, pix.height() / dpr / 2)
        transform = painter.worldTransform()
        painter.save()
        painter.resetTransform()
        for col in range(c0, c1 + 1):
            x = board.margin + col * board.pitch
            for row in range(r0, r1 + 1):
                y = board.margin + row * board.pitch
                painter.drawPixmap(transform.map(QPointF(x, -y)) - half, pix)
        painter.restore()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        """Hole column/row numbers along the board edges (constant pixel size)."""
        board = self.doc.layout.breadboard
        scale = view_scale(painter)
        pitch_px = board.pitch * scale
        if pitch_px < 4:
            return
        step = max(1, math.ceil(22 / pitch_px))
        if step > 1:
            step = 5 if step <= 5 else 10 * math.ceil(step / 10)
        font = QFont()
        font.setPointSizeF(7.5)
        transform = painter.worldTransform()
        painter.save()
        painter.resetTransform()
        painter.setFont(font)
        painter.setPen(C.GRID_LABEL)
        offset = 0.32
        for col in range(board.columns):
            if (col + 1) % step and col != 0:
                continue
            x = board.margin + col * board.pitch
            p = transform.map(QPointF(x, offset))
            painter.drawText(QRectF(p.x() - 15, p.y(), 30, 14), Qt.AlignmentFlag.AlignHCenter, str(col + 1))
        for row in range(board.rows):
            if (row + 1) % step and row != 0:
                continue
            y = board.margin + row * board.pitch
            p = transform.map(QPointF(-offset, -y))
            painter.drawText(QRectF(p.x() - 32, p.y() - 7, 30, 14), Qt.AlignmentFlag.AlignRight, str(row + 1))
        painter.restore()

    # ================================================================= sync
    def sync_all(self) -> None:
        self.update_scene_rect()
        live = {c.id for c in self.doc.layout.components}
        for cid in list(self.comp_items):
            if cid not in live:
                for item in self.comp_items.pop(cid).values():
                    self.removeItem(item)
                label = self.comp_labels.pop(cid, None)
                if label is not None:
                    self.removeItem(label)
        for comp in self.doc.layout.components:
            if comp.id not in self.comp_items:
                items = {layer: ComponentLayerItem(comp.id, layer) for layer in Layer}
                for item in items.values():
                    item.setVisible(False)  # until the first analysis gives it geometry
                    self.addItem(item)
                self.comp_items[comp.id] = items
                label = LabelItem(color=QColor("#e6e8eb"), background=QColor(15, 16, 19, 170))
                label.anchor = QPointF(8, 6)
                self.addItem(label)
                self.comp_labels[comp.id] = label
        self.update_component_poses(live)
        self.sync_annotations()
        self.apply_selection()
        self.invalidate(self.sceneRect(), QGraphicsScene.SceneLayer.BackgroundLayer)

    def update_component_poses(self, ids) -> None:
        for cid in ids:
            comp = self.doc.layout.component(cid)
            items = self.comp_items.get(cid)
            if comp is None or items is None:
                continue
            for item in items.values():
                item.set_pose(comp.x, comp.y, comp.angle)
            label = self.comp_labels[cid]
            label.setPos(to_scene(comp.x, comp.y))
            label.set_text(comp.label)
            label.setVisible(self.show_labels and bool(comp.label))
        self._update_selection_overlay()

    def sync_annotations(self) -> None:
        layout = self.doc.layout
        live_m = {m.id: m for m in layout.measurements}
        for mid in list(self.measure_items):
            if mid not in live_m:
                self.removeItem(self.measure_items.pop(mid))
        for mid, m in live_m.items():
            item = self.measure_items.get(mid)
            if item is None:
                item = MeasurementItem(m)
                self.addItem(item)
                self.measure_items[mid] = item
            else:
                item.set_points(m.a, m.b)
            item.selected = mid in self.selected_annotations
            item.update()
        live_p = {p.id: p for p in layout.probes}
        for pid in list(self.probe_items):
            if pid not in live_p:
                self.removeItem(self.probe_items.pop(pid))
        for pid in live_p:
            if pid not in self.probe_items:
                item = ProbeItem(pid)
                self.addItem(item)
                self.probe_items[pid] = item
        self.selected_annotations &= set(live_m) | set(live_p)
        self.update_probes()

    def apply_analysis(self) -> None:
        analysis = self.doc.analysis
        illegal_layers: dict[str, set[Layer]] = {}
        regions = []
        for issue in analysis.active_issues:  # ignored problems are not highlighted
            layers = {issue.layer} if issue.layer is not None else {Layer.BASE, Layer.OPTIC}
            for cid in issue.component_ids:
                illegal_layers.setdefault(cid, set()).update(layers)
            if issue.region is not None:
                regions.append(issue.region)

        for cid, items in self.comp_items.items():
            placed = analysis.units.get(cid)
            comp = self.doc.layout.component(cid)
            if placed is None or comp is None:
                continue
            key = (id(placed.build), placed.fork)
            for layer, item in items.items():
                if item.geometry_key != key:
                    item.set_geometry(placed, _beam_color_for(comp))
                    item.geometry_key = key
                visible = item.has_content() and (self.show_bases if layer is Layer.BASE else self.show_optics)
                item.setVisible(visible)
                item.opacity_override = 0.35 if (layer is Layer.OPTIC and self.ghost_optics) else 1.0
                is_illegal = layer in illegal_layers.get(cid, set())
                if item.illegal != is_illegal:
                    item.illegal = is_illegal
                    item.update()
        self.issue_item.set_regions(regions)
        self.beam_item.set_trace(analysis.trace, self.beam_exaggeration)
        self.beam_item.setVisible(self.show_beams)
        self.update_probes()
        self._update_selection_overlay()

    def apply_selection(self) -> None:
        selected = set(self.doc.selection)
        for cid, items in self.comp_items.items():
            for item in items.values():
                flag = cid in selected
                if item.selected != flag:
                    item.selected = flag
                    item.update()
        self._update_selection_overlay()

    def set_hovered(self, comp_id: str | None) -> None:
        if comp_id == self.hovered_id:
            return
        for cid in (self.hovered_id, comp_id):
            for item in self.comp_items.get(cid, {}).values():
                item.hovered = cid == comp_id
                item.update()
        self.hovered_id = comp_id

    def set_annotation_selection(self, ids: set[str]) -> None:
        self.selected_annotations = set(ids)
        for mid, item in self.measure_items.items():
            item.selected = mid in ids
            item.update()
        for pid, item in self.probe_items.items():
            item.selected = pid in ids
            item.update()

    def _update_selection_overlay(self) -> None:
        sel = self.doc.selected_components()
        if len(sel) != 1:
            self.selection_overlay.clear()
            return
        comp = sel[0]
        placed = self.doc.analysis.units.get(comp.id)
        optic = None
        if placed is not None and placed.build.elements:
            e = placed.build.elements[0]
            optic = tuple(comp.pose.apply(e.center))
        self.selection_overlay.set_target(comp.x, comp.y, comp.angle, optic)

    # ================================================================ probes
    def update_probes(self) -> None:
        trace = self.doc.analysis.trace
        for probe in self.doc.layout.probes:
            item = self.probe_items.get(probe.id)
            if item is None:
                continue
            hit = trace.nearest((probe.x, probe.y), max_distance=0.75)
            if hit is None:
                item.setPos(to_scene(probe.x, probe.y))
                item.active = False
                item.label.set_text(f"{probe.label}: no beam")
            else:
                seg, s, _ = hit
                item.setPos(to_scene(*seg.point_at(s)))
                item.active = True
                beam = seg.state_at(s).tangential
                item.label.set_text(f"{probe.label}  w {format_beam_size(beam.radius)}")
            item.update()

    # ============================================================== options
    def set_view_options(self, **options) -> None:
        for key, value in options.items():
            setattr(self, key, value)
        for cid, label in self.comp_labels.items():
            comp = self.doc.layout.component(cid)
            label.setVisible(self.show_labels and comp is not None and bool(comp.label))
        self.apply_analysis()

    # =============================================================== queries
    def component_at(self, scene_pos: QPointF) -> str | None:
        for item in self.items(scene_pos):
            if isinstance(item, ComponentLayerItem) and item.isVisible() and not item.ghost:
                return item.comp_id
        return None

    def annotation_at(self, scene_pos: QPointF, tolerance: float) -> str | None:
        area = QRectF(scene_pos.x() - tolerance, scene_pos.y() - tolerance, 2 * tolerance, 2 * tolerance)
        for item in self.items(area):
            if isinstance(item, ProbeItem):
                return item.ann_id
            if isinstance(item, MeasurementItem) and not item.preview:
                return item.ann_id
        return None

    def components_in(self, scene_rect: QRectF) -> list[str]:
        found: list[str] = []
        for item in self.items(scene_rect, Qt.ItemSelectionMode.IntersectsItemShape):
            if isinstance(item, ComponentLayerItem) and item.isVisible() and not item.ghost:
                if item.comp_id not in found:
                    found.append(item.comp_id)
        return found

    def beam_readout(self, x: float, y: float, tolerance: float) -> tuple[str, QPointF] | None:
        """Beam parameters near a model point, as label text + scene anchor."""
        hit = self.doc.analysis.trace.nearest((x, y), tolerance)
        if hit is None:
            return None
        seg, s, _ = hit
        state = seg.state_at(s)
        beam_t = state.tangential
        path = seg.path_start + s
        lines = [
            f"λ {state.wavelength * 25.4e6:.1f} nm   P {state.power:.3g} mW",
            f"w {format_beam_size(beam_t.radius)}   w₀ {format_beam_size(beam_t.waist_radius)}",
            f"to waist {format_optical_distance(-beam_t.distance_from_waist)}",
            f"R {format_optical_distance(beam_t.curvature_radius)}   path {path:.3f}\"",
        ]
        if abs(state.q_s - state.q_t) > 1e-9:
            lines.insert(2, f"w (sagittal) {format_beam_size(state.sagittal.radius)}")
        return "\n".join(lines), to_scene(*seg.point_at(s))
