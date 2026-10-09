r"""jos/views/holdings.py - what is already owned.

Phase 1's holdings view. The brief files it as "holdings view (without
health)", on the assumption the app would have to re-score the holdings
itself before it could say anything about their condition.

It does not have to. position_sizer already re-scores every holding on every
run - its exit review is the second half of the strategy, the part that asks
whether a thesis has quietly stopped being true - and writes the result into
sized_candidates.json as `holdings_review`. So the verdict column here is the
pipeline's own: hold, watch, exit_review, or unavailable, with the sentence
that produced it. Nothing is invented, and nothing is recomputed.

What this means for Phase 2: the brief's holdings-health module wants its own
re-scoring step into holdings_scored.json with its own review/watch/intact
thresholds. That would be a second opinion on a question the pipeline already
answers, with different words for the same three states. Worth settling before
building it - noted in the README.

Currencies are never summed across (section 12). The weight column is a share
of cost basis WITHIN each currency, and a mixed-currency portfolio gets a
labelled warning rather than a single meaningless total.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from .. import pipeline as pipe
from .. import theme
from ..datastore import describe_age, age_seconds, venue
from .panel import Message

COLUMNS = ("ticker", "venue", "shares", "cost basis", "weight",
           "score", "since", "status", "why")

COL_TICKER, COL_VENUE, COL_SHARES, COL_BASIS, COL_WEIGHT, \
    COL_SCORE, COL_SINCE, COL_STATUS, COL_WHY = range(9)

# The pipeline's verdicts, with the app's wording and colour. Its words are
# kept as the tooltip so the two are never out of step.
VERDICTS = {
    "hold": ("intact", theme.GREEN,
             "fundamentals still support the position"),
    "watch": ("watch", theme.AMBER,
              "worth a look: the score has slipped or two trend categories "
              "are deteriorating"),
    "exit_review": ("review", theme.RED,
                    "an exit trigger fired - the score is at or below the "
                    "exit band, the multi-year trend is deteriorating, or "
                    "the composite fell sharply since the last archived scan"),
    "unavailable": ("no data", theme.FAINT,
                    "this holding could not be re-scored on the last run"),
}


class HoldingsView(QWidget):
    """A table of holdings with the pipeline's own exit-review verdict."""

    selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scan = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.summary = QLabel("")
        self.summary.setObjectName("caption")
        head.addWidget(self.summary)
        head.addStretch(1)
        self.currency_note = QLabel("")
        self.currency_note.setObjectName("caption")
        head.addWidget(self.currency_note)
        layout.addLayout(head)

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
        for column, width in ((COL_TICKER, 86), (COL_VENUE, 62),
                              (COL_SHARES, 62), (COL_BASIS, 78),
                              (COL_WEIGHT, 64), (COL_SCORE, 56),
                              (COL_SINCE, 62), (COL_STATUS, 66)):
            self.table.setColumnWidth(column, width)

        self.table.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.table, 1)

        self.message = Message("")
        self.message.hide()
        layout.addWidget(self.message)

    def set_scan(self, scan):
        self._scan = scan
        holdings = scan.holdings if scan else []

        if not holdings:
            self.table.setRowCount(0)
            self.table.hide()
            self.summary.setText("")
            self.currency_note.setText("")
            self.message.setText(
                "no holdings on file. position_sizer writes a template to "
                "holdings.json on its first run - fill it in with real "
                "positions and the next scan will size candidates against "
                "them. Until then, sizing is not correlation-adjusted, "
                "which is the honest answer rather than a correlation of zero.")
            self.message.show()
            return

        self.message.hide()
        self.table.show()

        # Share of cost basis within each currency - never across.
        totals = {}
        for holding in holdings:
            currency = scan.holding_currency(holding)
            shares = pipe.num(holding.get("shares")) or 0.0
            basis = pipe.num(holding.get("cost_basis")) or 0.0
            totals[currency] = totals.get(currency, 0.0) + shares * basis

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(holdings))

        for row, holding in enumerate(holdings):
            ticker = holding.get("ticker") or ""
            currency = scan.holding_currency(holding)
            review = scan.review_by_ticker.get(ticker) or {}

            shares = pipe.num(holding.get("shares"))
            basis = pipe.num(holding.get("cost_basis"))
            value = (shares or 0.0) * (basis or 0.0)
            total = totals.get(currency) or 0.0
            weight = (100.0 * value / total) if total > 0 else None

            composite = pipe.num(review.get("composite"))
            if composite is None:
                candidate = scan.by_ticker.get(ticker) or {}
                composite = pipe.num(candidate.get("composite"))
            delta = pipe.num(review.get("composite_delta"))

            verdict = review.get("verdict") or ("unavailable" if not review
                                                else "hold")
            label, colour, tip = VERDICTS.get(
                verdict, (str(verdict), theme.FAINT, ""))

            reasons = review.get("reasons") or []
            why = str(reasons[0]) if reasons else (
                "no exit review on file for this holding" if not review else "")

            cells = [
                (COL_TICKER, ticker, None, ticker),
                (COL_VENUE, venue(ticker), theme.MUTED, venue(ticker)),
                (COL_SHARES, "" if shares is None else f"{shares:g}",
                 None, shares or 0),
                (COL_BASIS, "" if basis is None else f"{basis:,.2f} {currency}",
                 None, basis or 0),
                (COL_WEIGHT, "" if weight is None else f"{weight:.1f}%",
                 None, weight or 0),
                (COL_SCORE, "n/a" if composite is None else f"{composite:.2f}",
                 theme.score_colour(composite),
                 -1e9 if composite is None else composite),
                (COL_SINCE, "" if delta is None else f"{delta:+.2f}",
                 (theme.RED if delta is not None and delta < 0
                  else theme.GREEN if delta else theme.FAINT),
                 delta if delta is not None else 0),
                (COL_STATUS, label, colour, label),
                (COL_WHY, why, theme.MUTED, why),
            ]

            for column, text, colour_hex, sort_value in cells:
                item = QTableWidgetItem(str(text))
                item.setData(Qt.ItemDataRole.UserRole, sort_value)
                if colour_hex:
                    item.setForeground(QColor(colour_hex))
                if column == COL_STATUS:
                    rule = tip
                    if reasons:
                        rule = tip + "\n\nthis holding: " + "; ".join(
                            str(r) for r in reasons)
                    item.setToolTip(rule)
                elif column == COL_SINCE:
                    previous = pipe.num(review.get("previous_composite"))
                    item.setToolTip(
                        "change since the most recent archived scan"
                        + (f" (was {previous:.2f})" if previous is not None else ""))
                elif column == COL_BASIS:
                    item.setToolTip(
                        f"average price per share, in {currency}. "
                        f"Cost-basis totals are grouped by currency and "
                        f"never summed across them.")
                elif column == COL_WEIGHT:
                    item.setToolTip(f"share of {currency} cost basis")
                if column in (COL_SHARES, COL_BASIS, COL_WEIGHT, COL_SCORE,
                              COL_SINCE):
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight
                                              | Qt.AlignmentFlag.AlignVCenter))
                self.table.setItem(row, column, item)

        self.table.setSortingEnabled(True)

        counts = {}
        for holding in holdings:
            review = scan.review_by_ticker.get(holding.get("ticker")) or {}
            verdict = review.get("verdict") or "unavailable"
            counts[verdict] = counts.get(verdict, 0) + 1

        parts = [f"{len(holdings)} holding(s)"]
        for verdict in ("exit_review", "watch", "hold"):
            if counts.get(verdict):
                parts.append(f"{counts[verdict]} {VERDICTS[verdict][0]}")
        age = scan.age
        parts.append(f"reviewed {describe_age(age)}")
        self.summary.setText(" · ".join(parts))

        if scan.mixed_currency:
            currencies = sorted({scan.holding_currency(h) for h in holdings})
            self.currency_note.setText(
                "mixed currency (" + " / ".join(currencies)
                + "), not converted")
            self.currency_note.setStyleSheet(f"color: {theme.AMBER};")
        else:
            self.currency_note.setText("")

    def _on_selection(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), COL_TICKER)
        if item is not None and item.text():
            self.selected.emit(item.text())
