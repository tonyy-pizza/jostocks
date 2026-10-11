r"""jos/views/holdings.py - what is already owned, and whether it still holds.

Columns are section 6's: ticker (with its name and currency), weight, the
score a month ago against the score now, status, the next report date, and
why. Status comes from jos.health, which applies the section 10 thresholds
against the history snapshot closest to `health_lookback_days` ago.

TWO VERDICTS, AND WHY BOTH ARE HERE
-----------------------------------
position_sizer writes its own verdict into sized_candidates.json as
`holdings_review`, and this view does not throw it away. The two answer
different questions:

    the pipeline's    compares against the most recent archived run, so it
                      says "has this changed since the last scan"
    this view's       compares against a month ago, so it says "has this
                      drifted over a month"

A holding losing 0.3 a week never trips the pipeline's 1.5-point drop between
two consecutive runs, and is down 1.5 here. The status column is the monthly
read, because that is the one the panel exists for; the pipeline's verdict is
in the status tooltip, named as such.

Currencies are never summed across (section 12). Weight is a share of cost
basis WITHIN each currency, and a mixed portfolio is labelled instead of
added up.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from .. import earnings as earnings_module
from .. import health as health_module
from .. import pipeline as pipe
from .. import theme
from ..datastore import describe_age, venue
from .panel import Chip, Message

COLUMNS = ("ticker", "weight", "cost basis", "score, 30d → now",
           "status", "next report", "why")

COL_TICKER, COL_WEIGHT, COL_BASIS, COL_SCORE, COL_STATUS, \
    COL_REPORT, COL_WHY = range(7)

#: The app's own monthly verdicts, with their colour. The word carries the
#: meaning; colour only reinforces it.
STATUS_TINT = {
    health_module.REVIEW: theme.RED,
    health_module.WATCH: theme.AMBER,
    health_module.INTACT: theme.GREEN,
    health_module.BUILDING: theme.FAINT,
    health_module.NO_DATA: theme.FAINT,
}

#: The pipeline's own exit-review verdicts, for the tooltip.
PIPELINE_VERDICT = {
    "hold": "fundamentals still support the position",
    "watch": "worth a look",
    "exit_review": "an exit trigger fired",
    "unavailable": "could not be re-scored on the last run",
}


class HoldingsView(QWidget):
    """Holdings with a monthly health read."""

    selected = pyqtSignal(str)
    rescore_requested = pyqtSignal()

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self._scan = None
        self._settings = settings
        self._rows = []
        self._earnings = {}
        self._rescored = {}

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

        self.rescore_chip = Chip("re-score")
        self.rescore_chip.setToolTip(
            "Run every holding through stage 1 again and fetch its next "
            "report date. Uses the evaluator's own single-ticker path, off "
            "the UI thread, cached to data/holdings_scored.json.")
        self.rescore_chip.clicked.connect(self.rescore_requested.emit)
        head.addWidget(self.rescore_chip)
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
        for column, width in ((COL_TICKER, 100), (COL_WEIGHT, 62),
                              (COL_BASIS, 96), (COL_SCORE, 112),
                              (COL_STATUS, 78), (COL_REPORT, 104)):
            self.table.setColumnWidth(column, width)

        self.table.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.table, 1)

        self.message = Message("")
        self.message.hide()
        layout.addWidget(self.message)

        self.footer = QLabel("")
        self.footer.setObjectName("faint")
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)

    # ── data in ─────────────────────────────────────────────────────
    def set_rescored(self, records):
        """Fresh stage-1 records from the re-score worker."""
        self._rescored = dict(records or {})
        self.set_scan(self._scan)

    def set_earnings(self, dates):
        """{ticker: {date, timing}} from the earnings worker."""
        self._earnings = dict(dates or {})
        self.set_scan(self._scan)

    def set_scan(self, scan, history_days=None, baseline=None):
        self._scan = scan
        if history_days is not None:
            self._history_days = history_days
        if baseline is not None:
            self._baseline = baseline

        holdings = scan.holdings if scan else []

        if not holdings:
            self.table.setRowCount(0)
            self.table.hide()
            self.summary.setText("")
            self.currency_note.setText("")
            self.footer.setText("")
            self.rescore_chip.setEnabled(False)
            self.message.setText(
                "no holdings on file. position_sizer writes a template to "
                "holdings.json on its first run - fill it in with real "
                "positions and the next scan will size candidates against "
                "them. Until then, sizing is not correlation-adjusted, "
                "which is the honest answer rather than a correlation of "
                "zero.")
            self.message.show()
            return

        self.message.hide()
        self.table.show()
        self.rescore_chip.setEnabled(True)

        limits = health_module.thresholds(self._settings) if self._settings \
            else health_module.thresholds(_Defaults())
        history_days = getattr(self, "_history_days", None)
        baseline = getattr(self, "_baseline", None) or {}

        # Now: the fresh re-score where there is one, else the scan's own
        # record for the name.
        current = {}
        for holding in holdings:
            ticker = holding.get("ticker")
            record = (self._rescored.get(ticker)
                      or scan.scored_by_ticker.get(ticker)
                      or scan.by_ticker.get(ticker)
                      or scan.review_by_ticker.get(ticker)
                      or {})
            current[ticker] = record

        self._rows = health_module.assess(
            holdings, current, baseline, limits, history_days=history_days,
            earnings=self._earnings)

        # Weight within each currency, never across.
        totals = {}
        for holding in holdings:
            currency = scan.holding_currency(holding)
            shares = pipe.num(holding.get("shares")) or 0.0
            basis = pipe.num(holding.get("cost_basis")) or 0.0
            totals[currency] = totals.get(currency, 0.0) + shares * basis

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._rows))

        rule_text = health_module.describe_rules(limits)

        for index, row in enumerate(self._rows):
            ticker = row["ticker"]
            holding = row["holding"]
            record = row["record"] or {}
            currency = scan.holding_currency(holding)

            shares = pipe.num(holding.get("shares"))
            basis = pipe.num(holding.get("cost_basis"))
            value = (shares or 0.0) * (basis or 0.0)
            total = totals.get(currency) or 0.0
            weight = (100.0 * value / total) if total > 0 else None

            before, after = row.get("before"), row.get("after")
            if before is not None and after is not None:
                score_text = f"{before:.2f} → {after:.2f}"
            elif after is not None:
                score_text = f"→ {after:.2f}"
            else:
                score_text = "n/a"

            status = row["status"]
            tint = STATUS_TINT.get(status, theme.FAINT)
            report = earnings_module.describe(row.get("next_report"))
            why = "; ".join(str(r) for r in (row.get("reasons") or []))

            name = record.get("name") or ""
            label = f"{ticker}"

            cells = (
                (COL_TICKER, label, None, ticker),
                (COL_WEIGHT, "" if weight is None else f"{weight:.1f}%",
                 None, weight or 0),
                (COL_BASIS, "" if basis is None else f"{basis:,.2f} {currency}",
                 None, basis or 0),
                (COL_SCORE, score_text, theme.score_colour(after),
                 -1e9 if after is None else after),
                (COL_STATUS, status, tint, status),
                (COL_REPORT, report,
                 theme.FAINT if report == earnings_module.UNKNOWN else None,
                 str((row.get("next_report") or {}).get("date") or "zzzz")),
                (COL_WHY, why, theme.MUTED, why),
            )

            for column, text, colour, sort_value in cells:
                item = QTableWidgetItem(str(text))
                item.setData(Qt.ItemDataRole.UserRole, sort_value)
                if colour:
                    item.setForeground(QColor(colour))
                if column in (COL_WEIGHT, COL_BASIS, COL_SCORE):
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight
                                              | Qt.AlignmentFlag.AlignVCenter))

                if column == COL_TICKER:
                    tip = [f"{ticker} · {venue(ticker)} · {currency}"]
                    if name:
                        tip.append(name)
                    if shares is not None:
                        tip.append(f"{shares:g} shares")
                    if holding.get("entry_date"):
                        tip.append(f"opened {holding['entry_date']}")
                    if holding.get("entry_price") is not None:
                        tip.append(f"entry {holding['entry_price']:,.2f}")
                    item.setToolTip("\n".join(tip))

                elif column == COL_STATUS:
                    tip = [f"{status}: " + "; ".join(row.get("reasons") or [])]
                    review = scan.review_by_ticker.get(ticker) or {}
                    verdict = review.get("verdict")
                    if verdict:
                        reasons = "; ".join(str(r) for r in
                                            (review.get("reasons") or []))
                        tip.append(
                            f"\nThe pipeline's own exit review, which "
                            f"compares against the last archived scan rather "
                            f"than a month ago, says “{verdict}”"
                            + (f": {reasons}" if reasons else "")
                            + f". {PIPELINE_VERDICT.get(verdict, '')}".rstrip())
                    tip.append("\n" + rule_text)
                    item.setToolTip("\n".join(tip))

                elif column == COL_SCORE:
                    delta = row.get("delta")
                    tip = [f"composite now {after:.2f}" if after is not None
                           else "no composite now"]
                    if before is not None:
                        tip.append(
                            f"{before:.2f} in the snapshot nearest "
                            f"{limits['lookback_days']:.0f} days ago")
                    if delta is not None:
                        tip.append(f"{delta:+.2f} over the window")
                    item.setToolTip("\n".join(tip))

                elif column == COL_WEIGHT:
                    item.setToolTip(
                        f"share of {currency} cost basis. Totals are grouped "
                        f"by currency and never summed across them.")

                elif column == COL_BASIS:
                    item.setToolTip(
                        f"average price per share, in {currency}")

                elif column == COL_REPORT:
                    entry = row.get("next_report") or {}
                    if entry.get("date"):
                        item.setToolTip(
                            f"next report {entry['date']}"
                            + (f", {entry['timing']}" if entry.get("timing")
                               else ""))
                    else:
                        item.setToolTip(
                            "Yahoo has no date for this name. Press re-score "
                            "to look again; it is never guessed.")

                self.table.setItem(index, column, item)

        self.table.setSortingEnabled(True)
        self._render_summary(scan, holdings, limits, history_days)

    def _render_summary(self, scan, holdings, limits, history_days):
        counts = {}
        for row in self._rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1

        parts = [f"{len(holdings)} holding(s)"]
        for status in (health_module.REVIEW, health_module.WATCH,
                       health_module.INTACT, health_module.BUILDING,
                       health_module.NO_DATA):
            if counts.get(status):
                parts.append(f"{counts[status]} {status}")
        self.summary.setText(" · ".join(parts))

        if scan.mixed_currency:
            currencies = sorted({scan.holding_currency(h) for h in holdings})
            self.currency_note.setText(
                "mixed currency (" + " / ".join(currencies)
                + "), not converted")
            self.currency_note.setStyleSheet(f"color: {theme.AMBER};")
        else:
            self.currency_note.setText("")

        bits = []
        if history_days is None:
            bits.append("no history yet, so no monthly comparison is possible")
        elif history_days < limits["min_history_days"]:
            bits.append(
                f"history is {history_days:.0f} day(s) deep; a status needs "
                f"{limits['min_history_days']:.0f}, so these read "
                f"“{health_module.BUILDING}”")
        else:
            bits.append(f"compared against {history_days:.0f} day(s) of history")

        if self._rescored:
            bits.append(f"{len(self._rescored)} re-scored this session")
        else:
            bits.append(f"scores from the scan, {describe_age(scan.age)}")
        self.footer.setText(" · ".join(bits))

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

    def holding_tickers(self):
        return [row["ticker"] for row in self._rows]


class _Defaults:
    """Stand-in settings, so the view works before one is handed over."""

    def get(self, key, default=None):
        from ..state import DEFAULTS
        return DEFAULTS.get(key, default)

    def green_from(self):
        return pipe.colour_thresholds()[0]
