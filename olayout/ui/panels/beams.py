"""Beam probes: a table of beam parameters plus the beam-size profile along
the optical path leading to the selected probe."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from olayout.core.units import format_beam_size, format_optical_distance, to_mm
from olayout.optics.tracer import BeamSegment
from olayout.ui.document import LayoutDocument
from olayout.ui.theme import C, wavelength_color

COLUMNS = ("Probe", "w in-plane", "w out-of-plane", "To waist", "Waist w₀", "R", "Path", "Power")


class BeamProfilePlot(QWidget):
    """1/e² radius versus path length along one beam path."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self._curves: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        self._marks: list[tuple[float, str]] = []
        self._probe: float | None = None
        self._color = QColor("#ff5a5a")
        self._message = "Add a probe on a beam (P) to see its profile."

    def set_path(self, chain: list[BeamSegment], probe_path: float | None, labels: dict[str, str]) -> None:
        self._curves = []
        self._marks = []
        self._probe = probe_path
        if not chain:
            self._message = "Add a probe on a beam (P) to see its profile."
            self.update()
            return
        self._message = ""
        self._color = wavelength_color(chain[0].state.wavelength * 25.4e6)
        for seg in chain:
            s = np.linspace(0.0, seg.length, 80)
            z = seg.path_start + s
            w_t = np.array([seg.state.propagate(v).tangential.radius for v in s])
            w_s = np.array([seg.state.propagate(v).sagittal.radius for v in s])
            self._curves.append((z, to_mm(w_t), to_mm(w_s)))
            if seg.end_owner and seg is not chain[-1]:
                self._marks.append((seg.path_start + seg.length, labels.get(seg.end_owner, "")))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), C.PANEL)
        font = QFont()
        font.setPointSizeF(7.5)
        p.setFont(font)
        if self._message:
            p.setPen(C.TEXT_DIM)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            return
        plot = QRectF(self.rect()).adjusted(46, 12, -12, -26)
        z_all = np.concatenate([c[0] for c in self._curves])
        w_all = np.concatenate([np.maximum(c[1], c[2]) for c in self._curves])
        z0, z1 = float(z_all.min()), float(z_all.max())
        w_max = float(w_all.max()) * 1.12 or 1.0
        if z1 - z0 < 1e-9:
            z1 = z0 + 1.0

        def to_px(z, w):
            return QPointF(plot.left() + (z - z0) / (z1 - z0) * plot.width(), plot.bottom() - w / w_max * plot.height())

        # Grid + axes
        p.setPen(QPen(C.BORDER, 1))
        for k in range(5):
            y = plot.top() + k * plot.height() / 4
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        p.setPen(C.TEXT_DIM)
        for k in range(5):
            value = w_max * (4 - k) / 4
            y = plot.top() + k * plot.height() / 4
            p.drawText(QRectF(0, y - 7, plot.left() - 6, 14), Qt.AlignmentFlag.AlignRight, f"{value:.2f}")
        ticks = _nice_ticks(z0, z1, 6)
        for t in ticks:
            x = to_px(t, 0).x()
            p.drawText(QRectF(x - 25, plot.bottom() + 4, 50, 14), Qt.AlignmentFlag.AlignHCenter, f"{t:g}\"")
        p.drawText(QRectF(2, plot.top() - 12, 60, 12), Qt.AlignmentFlag.AlignLeft, "w (mm)")
        # Optic markers
        for z, label in self._marks:
            x = to_px(z, 0).x()
            p.setPen(QPen(QColor(255, 255, 255, 50), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.setPen(C.TEXT_DIM)
            p.drawText(QRectF(x - 30, plot.top(), 60, 12), Qt.AlignmentFlag.AlignHCenter, label)
        # Curves
        for z, w_t, w_s in self._curves:
            for values, style in ((w_s, Qt.PenStyle.DashLine), (w_t, Qt.PenStyle.SolidLine)):
                if style is Qt.PenStyle.DashLine and np.allclose(values, w_t, rtol=1e-6):
                    continue
                path = QPainterPath(to_px(z[0], values[0]))
                for zi, wi in zip(z[1:], values[1:]):
                    path.lineTo(to_px(zi, wi))
                p.setPen(QPen(self._color, 1.8, style))
                p.drawPath(path)
        if self._probe is not None:
            x = to_px(self._probe, 0).x()
            p.setPen(QPen(C.PROBE, 1.4))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        p.end()


def _nice_ticks(lo: float, hi: float, count: int) -> list[float]:
    span = hi - lo
    raw = span / max(count, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=mag * 10)
    start = math.ceil(lo / step) * step
    ticks = []
    t = start
    while t <= hi + 1e-9:
        ticks.append(round(t, 6))
        t += step
    return ticks


class BeamPanel(QWidget):
    probeFocusRequested = Signal(float, float)

    def __init__(self, doc: LayoutDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.summary = QLabel()
        self.summary.setObjectName("Dim")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(90)
        self.table.itemSelectionChanged.connect(self._update_plot)
        self.table.cellDoubleClicked.connect(self._focus_probe)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        delete = QPushButton("Delete probe")
        delete.clicked.connect(self._delete_selected)
        clear = QPushButton("Clear probes && measurements")
        clear.clicked.connect(self.doc.clear_annotations)
        buttons.addWidget(delete)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.plot = BeamProfilePlot()
        layout.addWidget(self.plot, 2)

        doc.analysisUpdated.connect(self.refresh)
        doc.annotationsChanged.connect(self.refresh)
        doc.layoutReset.connect(self.refresh)
        self.refresh()

    def _probe_hits(self):
        trace = self.doc.analysis.trace
        for probe in self.doc.layout.probes:
            yield probe, trace.nearest((probe.x, probe.y), 0.75)

    def refresh(self) -> None:
        trace = self.doc.analysis.trace
        warnings = self.doc.analysis.active_warnings  # ignored ones are left out
        sources = {s.source for s in trace.segments}
        blocked = sum(1 for s in trace.segments if s.end_event == "blocked")
        self.summary.setText(
            f"{len(sources)} source(s) · {len(trace.segments)} beam segment(s) · {blocked} blocked"
            + (f" · {len(warnings)} warning(s): " + "; ".join(w.message for w in warnings[:3]) if warnings else "")
        )
        selected_row = self.table.currentRow()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for probe, hit in self._probe_hits():
            row = self.table.rowCount()
            self.table.insertRow(row)
            if hit is None:
                values = [probe.label, "no beam", "", "", "", "", "", ""]
            else:
                seg, s, _ = hit
                state = seg.state_at(s)
                t, sg = state.tangential, state.sagittal
                values = [
                    probe.label,
                    format_beam_size(t.radius),
                    format_beam_size(sg.radius),
                    format_optical_distance(-t.distance_from_waist),
                    format_beam_size(t.waist_radius),
                    format_optical_distance(t.curvature_radius),
                    f"{seg.path_start + s:.3f}\"",
                    f"{state.power:.4g} mW",
                ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, probe.id)
                self.table.setItem(row, col, item)
        if 0 <= selected_row < self.table.rowCount():
            self.table.selectRow(selected_row)
        elif self.table.rowCount():
            self.table.selectRow(self.table.rowCount() - 1)
        self.table.blockSignals(False)
        self._update_plot()

    def _selected_probe_id(self) -> str | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _update_plot(self) -> None:
        pid = self._selected_probe_id()
        labels = {c.id: c.label for c in self.doc.layout.components}
        for probe, hit in self._probe_hits():
            if probe.id == pid and hit is not None:
                seg, s, _ = hit
                self.plot.set_path(self.doc.analysis.trace.path_to(seg), seg.path_start + s, labels)
                return
        self.plot.set_path([], None, labels)

    def _delete_selected(self) -> None:
        pid = self._selected_probe_id()
        if pid:
            self.doc.delete_annotations([pid])

    def _focus_probe(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        if item is None:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        for probe in self.doc.layout.probes:
            if probe.id == pid:
                self.probeFocusRequested.emit(probe.x, probe.y)
