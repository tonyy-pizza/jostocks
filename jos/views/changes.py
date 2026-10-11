r"""jos/views/changes.py - what moved since the last scan.

A table of the four kinds of change with filter chips above it, and a plain
sentence when there is no earlier snapshot to compare against - which is the
normal state on a fresh install and should read as such rather than as an
empty table that might be a bug.

The score column shows `before -> after` rather than two columns, because the
pair is one fact and splitting it makes the eye do work. New and dropped names
have only one side, and show it on the side it belongs to.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from .. import changes as changes_module
from .. import pipeline as pipe
from .. import theme
from ..datastore import describe_age, age_seconds, venue
from .panel import Chip, Message

COLUMNS = ("ticker", "change", "score", "delta", "why")
COL_TICKER, COL_KIND, COL_SCORE, COL_DELTA, COL_WHY = range(5)

#: Each kind's word and colour. The word carries the meaning; the colour only
#: reinforces it, per section 5.
KINDS = {
    "new": ("new", theme.CYAN),
    "up": ("up", theme.GREEN),
    "down": ("down", theme.AMBER),
    "dropped": ("dropped", theme.RED),
}

FILTERS = (("all", "all"), ("new", "new"), ("up", "up"), ("down", "down"),
           ("dropped", "dropped"))


class ChangesView(QWidget):
    """The changes-since-last-scan table."""

    selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter = "all"
        self._result = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ── filter chips ────────────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(4)
        self._chips = {}
        for key, label in FILTERS:
            chip = Chip(label, checkable=True)
            chip.clicked.connect(lambda _c, k=key: self._set_filter(k))
            self._chips[key] = chip
            top.addWidget(chip)
        self._chips["all"].setChecked(True)

        top.addStretch(1)
        self.against = QLabel("")
        self.against.setObjectName("faint")
        top.addWidget(self.against)
        layout.addLayout(top)

        # ── table ───────────────────────────────────────────────────
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_WHY, QHeaderView.ResizeMode.Stretch)
        for column, width in ((COL_TICKER, 96), (COL_KIND, 74),
                              (COL_SCORE, 118), (COL_DELTA, 60)):
            self.table.setColumnWidth(column, width)

        self.table.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.table, 1)

        self.message = Message("")
        self.message.hide()
        layout.addWidget(self.message)

    # ── data in ─────────────────────────────────────────────────────
    def set_result(self, result):
        """Take a jos.changes.compare() result and draw it."""
        self._result = result or {}
        self._render()

    def _set_filter(self, key):
        self._filter = key
        for name, chip in self._chips.items():
            chip.setChecked(name == key)
        self._render()

    def _render(self):
        result = self._result or {}

        if not result.get("available"):
            self.table.setRowCount(0)
            self.table.hide()
            for chip in self._chips.values():
                chip.setEnabled(False)
            self.against.setText("")
            self.message.setText(result.get("reason")
                                 or "nothing to compare against yet")
            self.message.show()
            return

        self.message.hide()
        self.table.show()
        counts = result.get("counts") or {}
        for key, chip in self._chips.items():
            chip.setEnabled(True)
            if key == "all":
                total = sum(counts.values())
                chip.setText(f"all {total}" if total else "all")
            else:
                count = counts.get(key, 0)
                chip.setText(f"{key} {count}" if count else key)

        before_at = result.get("before_at")
        self.against.setText(
            f"against the scan from {describe_age(age_seconds(before_at))}"
            if before_at else "")

        rows = [r for r in (result.get("rows") or [])
                if self._filter == "all" or r["kind"] == self._filter]

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))

        for index, row in enumerate(rows):
            ticker = row["ticker"]
            word, colour = KINDS.get(row["kind"], (row["kind"], theme.MUTED))

            before, after = row.get("before"), row.get("after")
            if before is not None and after is not None:
                score_text = f"{before:.2f} → {after:.2f}"
            elif after is not None:
                score_text = f"→ {after:.2f}"
            elif before is not None:
                score_text = f"{before:.2f} →"
            else:
                score_text = ""

            delta = row.get("delta")
            delta_text = "" if delta is None else f"{delta:+.2f}"

            label = f"{ticker}  held" if row.get("already_held") else ticker
            why = "; ".join(str(r) for r in (row.get("reasons") or []))

            cells = (
                (COL_TICKER, label,
                 theme.CYAN if row.get("already_held") else None, ticker),
                (COL_KIND, word, colour, word),
                (COL_SCORE, score_text,
                 theme.score_colour(after if after is not None else before),
                 after if after is not None else (before or 0)),
                (COL_DELTA, delta_text,
                 theme.GREEN if (delta or 0) > 0
                 else theme.RED if (delta or 0) < 0 else theme.FAINT,
                 delta if delta is not None else 0),
                (COL_WHY, why, theme.MUTED, why),
            )

            for column, text, tint, sort_value in cells:
                item = QTableWidgetItem(str(text))
                item.setData(Qt.ItemDataRole.UserRole, sort_value)
                if tint:
                    item.setForeground(QColor(tint))
                if column in (COL_SCORE, COL_DELTA):
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight
                                              | Qt.AlignmentFlag.AlignVCenter))
                if column == COL_TICKER:
                    tip = [f"{ticker} · {venue(ticker)}"]
                    if row.get("name"):
                        tip.append(row["name"])
                    if row.get("sector"):
                        tip.append(row["sector"])
                    item.setToolTip("\n".join(tip))
                elif column == COL_WHY and why:
                    item.setToolTip("\n".join(str(r) for r in row["reasons"]))
                elif column == COL_KIND:
                    item.setToolTip({
                        "new": "not in the previous scan",
                        "up": "composite higher than the previous scan",
                        "down": "composite lower than the previous scan",
                        "dropped": "was in the previous scan, is not in this one",
                    }.get(row["kind"], ""))
                self.table.setItem(index, column, item)

        self.table.setSortingEnabled(True)

        if not rows:
            self.message.setText(
                f"nothing {self._filter}"
                if self._filter != "all"
                else "no name moved by more than "
                     f"{changes_module.MOVE_EPSILON:.2f} since the last scan")
            self.message.show()
        else:
            self.message.hide()

    def _on_selection(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), COL_TICKER)
        if item is None:
            return
        ticker = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if ticker:
            self.selected.emit(ticker)
