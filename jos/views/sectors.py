r"""jos/views/sectors.py - the sector ratings panel.

A narrow native panel: one row per sector, best average first, with a basis
toggle across the top. Clicking a sector asks the window to filter the
candidates table to it; clicking the same sector again clears the filter.

The panel is narrow by design (row 2, left of ticker detail), so the rows are
painted into a compact grid rather than a QTableView - a table's header,
scrollbar and selection chrome take more width than the data does at this
size. Each row is a button so it is reachable by keyboard, which a clickable
label would not be.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import pipeline as pipe
from .. import sectors as sectors_module
from .. import theme
from .panel import Chip, Message


class SectorRow(QPushButton):
    """One sector. Flat, full width, and selectable."""

    def __init__(self, row, basis, parent=None):
        super().__init__(parent)
        self.sector = row["sector"]
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

        average = row.get("average")
        colour = theme.score_colour(average)
        band = theme.score_band(average)

        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {theme.ROW_LINE};
                padding: 5px 4px;
                text-align: left;
            }}
            QPushButton:hover {{ background: {theme.BG}; }}
            QPushButton:checked {{ background: {theme.SELECTED}; }}
        """)

        layout = QGridLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(1)

        name = QLabel(str(row["sector"]).lower())
        name.setStyleSheet(f"color: {theme.CREAM}; background: transparent;")
        name.setWordWrap(True)
        layout.addWidget(name, 0, 0)

        score = QLabel("n/a" if average is None else f"{average:.2f}")
        score.setObjectName("mono")
        score.setStyleSheet(f"color: {colour}; background: transparent;")
        score.setAlignment(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(score, 0, 1)

        detail = f"{row['count']} name" + ("s" if row["count"] != 1 else "")
        if row.get("held"):
            detail += f", {row['held']} held"
        if row.get("best"):
            detail += f" · best {row['best']}"
        caption = QLabel(detail)
        caption.setObjectName("faint")
        caption.setStyleSheet(f"color: {theme.FAINT}; background: transparent;")
        layout.addWidget(caption, 1, 0, 1, 2)

        layout.setColumnStretch(0, 1)

        tip = [f"{row['sector']}: {row['count']} candidate(s)"]
        if average is not None:
            tip.append(f"average {basis} {average:.2f} — {band}, "
                       f"over {row['measured']} measured")
        if row.get("unmeasured"):
            tip.append(f"{row['unmeasured']} name(s) had no {basis} "
                       f"to average")
        if row.get("best"):
            tip.append(f"best: {row['best']} at {row['best_value']:.2f}")
        tip.append("click to filter the candidates table to this sector")
        self.setToolTip("\n".join(tip))


class SectorsView(QWidget):
    """The sector ratings body: a basis toggle, then the rows."""

    sector_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._basis = sectors_module.COMPOSITE
        self._bases = []
        self._rows = {}
        self._active = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._toggle_row = QHBoxLayout()
        self._toggle_row.setSpacing(3)
        layout.addLayout(self._toggle_row)

        self.message = Message("")
        self.message.hide()
        layout.addWidget(self.message)

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self._rows_layout = QVBoxLayout(holder)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch(1)
        scroller.setWidget(holder)
        layout.addWidget(scroller, 1)

        self.footer = QLabel("")
        self.footer.setObjectName("faint")
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)

    # ── data in ─────────────────────────────────────────────────────
    def set_scan(self, scan):
        self._scan = scan
        candidates = scan.candidates if scan else []

        # The basis toggle is rebuilt from the data, so a scoring change that
        # adds or drops a dimension is reflected without a code change.
        bases = sectors_module.available_bases(candidates)
        if bases != self._bases:
            self._bases = bases
            self._rebuild_toggle()
        if self._basis not in [key for key, _ in bases]:
            self._basis = sectors_module.COMPOSITE

        self._render()

    def _rebuild_toggle(self):
        while self._toggle_row.count():
            item = self._toggle_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        self._chips = {}
        for key, label in self._bases:
            chip = Chip(label, checkable=True)
            chip.setToolTip(
                "average the composite over each sector" if key == "composite"
                else f"average the {label} dimension over each sector")
            chip.clicked.connect(lambda _c, k=key: self._set_basis(k))
            self._chips[key] = chip
            self._toggle_row.addWidget(chip)
        self._toggle_row.addStretch(1)

    def _set_basis(self, key):
        self._basis = key
        self._render()

    def _render(self):
        for key, chip in getattr(self, "_chips", {}).items():
            chip.setChecked(key == self._basis)

        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self._rows = {}

        candidates = self._scan.candidates if self._scan else []
        if not candidates:
            self.message.setText(
                self._scan.empty_reason if self._scan
                else "no scan loaded yet")
            self.message.show()
            self.footer.setText("")
            return

        self.message.hide()
        summary = sectors_module.summarise(candidates, self._basis)

        for index, row in enumerate(summary):
            widget = SectorRow(row, self._basis)
            widget.setChecked(row["sector"] == self._active)
            widget.clicked.connect(
                lambda _c, s=row["sector"]: self.sector_clicked.emit(s))
            self._rows_layout.insertWidget(index, widget)
            self._rows[row["sector"]] = widget

        unmeasured = sum(r.get("unmeasured") or 0 for r in summary)
        parts = [f"{len(summary)} sector(s)"]
        if self._basis != sectors_module.COMPOSITE:
            parts.append(f"by {self._basis.lower()}")
        if unmeasured:
            parts.append(f"{unmeasured} name(s) unmeasured on this basis")
        self.footer.setText(" · ".join(parts))

    # ── filter state, owned by the window ───────────────────────────
    def set_active(self, sector):
        """Mark which sector the candidates table is filtered to."""
        self._active = sector or None
        for name, widget in self._rows.items():
            widget.setChecked(name == self._active)
