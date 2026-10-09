r"""jos/views/candidates.py - the ratings table.

Columns are section 6's: ticker (+ a held tag), score with a bar, liq, trend,
pattern, sent, guide -> adjusted, cut. Sortable and filterable, and the
selected row feeds ticker detail and the shared selection set.

The flag columns are not invented here. scan_report.flag_cells() decides what
liq, trend and pattern say and which colour each gets, and this table shows
the same words and glyphs so the app and the terminal agree:

    liq      ok liq / thin liq      from liquidity_flag
    trend    from roa_trend, NOT roa_trend_consistent. The strict flag
             demanded a non-decreasing ROA in every year it had, so it went
             red on one soft year in four and stayed green for a two-year-old
             listing with a single step to clear.
    pattern  the conviction verdict - the divergence pattern crossed with
             sentiment - because a price_disconnect nobody is talking about
             and one the public is actively negative on are different
             situations and size differently.
    sent     sentiment.json's overall, coloured on the composite thresholds.

Every glyph is paired with a word, because section 5 forbids relying on
colour alone. The model stores the colour in ForegroundRole and the word in
DisplayRole, so a screenshot in greyscale still reads.

Sorting uses a separate role rather than the displayed text: "cut 23%" and
"guide 3%-5%" sort as text in an order nobody wants, and a None has to sort
to one end rather than comparing as a string.
"""

from __future__ import annotations

from PyQt6.QtCore import (QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
                          Qt, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
                             QStyledItemDelegate, QTableView, QVBoxLayout, QWidget)

from .. import pipeline as pipe
from .. import sizing as sizing_module
from .. import theme
from ..datastore import venue
from .panel import Chip, Message

#: A role carrying the value to sort a column by.
SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
#: A role carrying the row's ticker, so a view can map selection to a name.
TICKER_ROLE = int(Qt.ItemDataRole.UserRole) + 2
#: A role carrying 0..10 for the score bar delegate.
BAR_ROLE = int(Qt.ItemDataRole.UserRole) + 3

COLUMNS = ("ticker", "score", "liq", "trend", "pattern", "sent",
           "guide → adjusted", "cut")

COL_TICKER, COL_SCORE, COL_LIQ, COL_TREND, COL_PATTERN, COL_SENT, \
    COL_GUIDE, COL_CUT = range(8)

# The conviction verdicts, as flag_cells renders them: a word and a glyph, so
# the meaning survives without colour. The pipeline's own text, shortened only
# where the terminal's fixed-width padding is not needed.
CONVICTION_CELLS = {
    "disconnect_supported": ("disconnect +", theme.GREEN,
                             "price low, trend intact, and the public read is "
                             "neutral or better - neglect rather than news"),
    "disconnect_unverified": ("disconnect ·", theme.GREEN,
                              "price low and trend intact; no confident "
                              "sentiment read on file"),
    "disconnect_contested": ("disconnect ?", theme.AMBER,
                             "price low and trend intact, but the public read "
                             "is negative - the market may be pricing "
                             "something the statements cannot show yet"),
    "trap_confirmed": ("value-trap !", theme.RED,
                       "fundamentals deteriorating and the public read agrees"),
    "trap_contested": ("trap / hype", theme.RED,
                       "fundamentals deteriorating while the public read is "
                       "positive - enthusiasm ahead of the numbers"),
    "trap_unverified": ("value-trap", theme.RED,
                        "price low and the multi-year trend is deteriorating; "
                        "no confident sentiment read on file"),
    "not_applicable": ("·", theme.FAINT,
                       "divergence pattern is neutral; nothing to cross"),
}

TREND_CELLS = {
    "improving": ("ok trend", theme.GREEN, "ROA improving across the window"),
    "flat": ("ok trend", theme.GREEN, "ROA flat across the window"),
    "mixed": ("~ trend", theme.AMBER, "ROA mixed across the window"),
    "deteriorating": ("x trend", theme.RED, "ROA declining across the window"),
}


class CandidatesModel(QAbstractTableModel):
    """The candidate rows, with sizing recomputed from the live controls.

    `set_sizing` is the hot path: it swaps the adjusted figures and emits a
    dataChanged over the two columns that moved, rather than resetting the
    model. A reset would drop the selection and the scroll position, which
    section 7 says must survive.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._sizing = {}
        self._sentiment = {}
        self._thresholds = pipe.colour_thresholds()

    # ── data in ─────────────────────────────────────────────────────
    def set_scan(self, scan):
        self.beginResetModel()
        self._rows = list(scan.candidates) if scan else []
        self._sentiment = dict(scan.sentiment) if scan else {}
        self._thresholds = pipe.colour_thresholds()
        self.endResetModel()

    def set_sizing(self, sizing_by_ticker):
        self._sizing = dict(sizing_by_ticker or {})
        if not self._rows:
            return
        top_left = self.index(0, COL_GUIDE)
        bottom_right = self.index(len(self._rows) - 1, COL_CUT)
        self.dataChanged.emit(top_left, bottom_right,
                              [Qt.ItemDataRole.DisplayRole,
                               Qt.ItemDataRole.ForegroundRole, SORT_ROLE])

    def candidate_at(self, row):
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def sizing_for(self, ticker):
        return self._sizing.get(ticker) or {}

    # ── Qt plumbing ─────────────────────────────────────────────────
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        if role == Qt.ItemDataRole.ToolTipRole:
            return {
                COL_TICKER: "symbol, with its exchange and a held tag",
                COL_SCORE: "composite score out of 10",
                COL_LIQ: "liquidity: whether a normal position would be a "
                         "large share of average daily dollar volume",
                COL_TREND: "direction of the multi-year ROA series",
                COL_PATTERN: "divergence pattern crossed with public sentiment",
                COL_SENT: "public sentiment, 0-10, centred on 5",
                COL_GUIDE: "base guidance, then what the sizing controls make of it",
                COL_CUT: "how much size the correlation cut removes",
            }.get(section)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        candidate = self._rows[index.row()]
        column = index.column()
        ticker = candidate.get("ticker")

        if role == TICKER_ROLE:
            return ticker

        composite = pipe.num(candidate.get("composite"))
        live = self._sizing.get(ticker) or candidate.get("sizing") or {}

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(candidate, live, column, composite)
        if role == SORT_ROLE:
            return self._sort_value(candidate, live, column, composite)
        if role == Qt.ItemDataRole.ForegroundRole:
            colour = self._colour(candidate, live, column, composite)
            return QColor(colour) if colour else None
        if role == BAR_ROLE and column == COL_SCORE:
            return composite
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(candidate, live, column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in (COL_SCORE, COL_SENT, COL_CUT):
                return int(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    # ── cell content ────────────────────────────────────────────────
    def _display(self, candidate, live, column, composite):
        ticker = candidate.get("ticker") or ""

        if column == COL_TICKER:
            return f"{ticker}  held" if candidate.get("already_held") else ticker
        if column == COL_SCORE:
            return "n/a" if composite is None else f"{composite:.2f}"
        if column == COL_LIQ:
            return "thin liq" if candidate.get("liquidity_flag") else "ok liq"
        if column == COL_TREND:
            return TREND_CELLS.get(candidate.get("roa_trend"),
                                   ("· trend", theme.FAINT, ""))[0]
        if column == COL_PATTERN:
            verdict = (candidate.get("sizing") or {}).get("conviction") \
                      or "not_applicable"
            return CONVICTION_CELLS.get(verdict, CONVICTION_CELLS
                                        ["not_applicable"])[0]
        if column == COL_SENT:
            overall = pipe.num((self._sentiment.get(ticker) or {}).get("overall"))
            return "n/a" if overall is None else f"{overall:.1f}"
        if column == COL_GUIDE:
            base = sizing_module.guide_short(live.get("base_guide"))
            adjusted = sizing_module.guide_short(live.get("adjusted_guide"))
            if not base:
                return ""
            return base if adjusted == base else f"{base} → {adjusted}"
        if column == COL_CUT:
            return sizing_module.cut_label(live)
        return ""

    def _sort_value(self, candidate, live, column, composite):
        """What a column sorts by. Never the displayed string.

        None sorts to the bottom on a descending sort, which is what you want
        for a score: unmeasured names should not lead the table.
        """
        if column == COL_TICKER:
            return candidate.get("ticker") or ""
        if column == COL_SCORE:
            return -1e9 if composite is None else composite
        if column == COL_LIQ:
            return 0 if candidate.get("liquidity_flag") else 1
        if column == COL_TREND:
            return {"improving": 3, "flat": 2, "mixed": 1,
                    "deteriorating": 0}.get(candidate.get("roa_trend"), -1)
        if column == COL_PATTERN:
            # Best read first on a descending sort.
            return {"disconnect_supported": 5, "disconnect_unverified": 4,
                    "not_applicable": 3, "disconnect_contested": 2,
                    "trap_unverified": 1, "trap_contested": 0,
                    "trap_confirmed": -1}.get(
                        (candidate.get("sizing") or {}).get("conviction"), 3)
        if column == COL_SENT:
            overall = pipe.num((self._sentiment.get(candidate.get("ticker")) or {})
                               .get("overall"))
            return -1e9 if overall is None else overall
        if column == COL_GUIDE:
            value = pipe.num(live.get("adjusted_high_pct"))
            return -1e9 if value is None else value
        if column == COL_CUT:
            value = pipe.num(live.get("reduction")) if live.get(
                "correlation_adjusted") else 0.0
            return value or 0.0
        return ""

    def _colour(self, candidate, live, column, composite):
        if column == COL_SCORE:
            return theme.score_colour(composite, self._thresholds)
        if column == COL_LIQ:
            return theme.RED if candidate.get("liquidity_flag") else theme.GREEN
        if column == COL_TREND:
            return TREND_CELLS.get(candidate.get("roa_trend"),
                                   ("", theme.FAINT, ""))[1]
        if column == COL_PATTERN:
            verdict = (candidate.get("sizing") or {}).get("conviction") \
                      or "not_applicable"
            return CONVICTION_CELLS.get(verdict,
                                        CONVICTION_CELLS["not_applicable"])[1]
        if column == COL_SENT:
            overall = pipe.num((self._sentiment.get(candidate.get("ticker")) or {})
                               .get("overall"))
            return theme.score_colour(overall, self._thresholds)
        if column == COL_CUT:
            return theme.AMBER if live.get("correlation_adjusted") else theme.FAINT
        if column == COL_TICKER and candidate.get("already_held"):
            return theme.CYAN
        return None

    def _tooltip(self, candidate, live, column):
        ticker = candidate.get("ticker") or ""
        if column == COL_TICKER:
            bits = [f"{ticker} · {venue(ticker)}"]
            if candidate.get("name"):
                bits.append(candidate["name"])
            if candidate.get("sector"):
                bits.append(candidate["sector"])
            if candidate.get("already_held"):
                bits.append("already held - shown, but not sized as a new position")
            return "\n".join(bits)
        if column == COL_SCORE:
            band = theme.score_band(pipe.num(candidate.get("composite")),
                                    self._thresholds)
            good, fair = self._thresholds
            return (f"{candidate.get('rating') or 'unrated'} — {band}\n"
                    f"green at {good:.2f}, amber at {fair:.2f}, "
                    f"red below (stock_evaluator's own thresholds)")
        if column == COL_LIQ:
            for warning in (candidate.get("warnings") or []):
                if "liquidity" in str(warning).lower():
                    return str(warning)
            return ("a normal position is a small share of average daily "
                    "dollar volume" if not candidate.get("liquidity_flag")
                    else "thin liquidity")
        if column == COL_TREND:
            years = candidate.get("trend_years_available")
            detail = TREND_CELLS.get(candidate.get("roa_trend"),
                                     ("", "", "no multi-year ROA series"))[2]
            return f"{detail}\n{years} year(s) of trend history" if years else detail
        if column == COL_PATTERN:
            stored = candidate.get("sizing") or {}
            verdict = stored.get("conviction") or "not_applicable"
            text = CONVICTION_CELLS.get(verdict,
                                        CONVICTION_CELLS["not_applicable"])[2]
            reason = (stored.get("conviction_detail") or {}).get("reason")
            scale = pipe.num(stored.get("conviction_scale"))
            lines = [text]
            if reason:
                lines.append(reason)
            if scale is not None and scale != 1.0:
                lines.append(f"size multiplied by {scale:.2f} for this read")
            return "\n".join(lines)
        if column == COL_SENT:
            scores = self._sentiment.get(ticker) or {}
            confidence = pipe.num(scores.get("confidence"))
            if not scores:
                return "no sentiment on file for this name"
            return (f"public sentiment {pipe.num(scores.get('overall'))}, "
                    f"confidence {confidence}")
        if column in (COL_GUIDE, COL_CUT):
            lines = []
            if live.get("base_guide"):
                lines.append(f"base: {live['base_guide']}")
            if live.get("adjusted_guide") != live.get("base_guide"):
                lines.append(f"adjusted: {live.get('adjusted_guide')}")
            if live.get("note"):
                lines.append(live["note"])
            flags = (candidate.get("sizing") or {}).get("risk_flags") or []
            if flags:
                lines.append("flags: " + ", ".join(str(f) for f in flags))
            return "\n".join(lines) or None
        return None


class CandidatesProxy(QSortFilterProxyModel):
    """Sorting on SORT_ROLE, plus a text filter and a sector filter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._sector = None
        self._text = ""

    def set_sector(self, sector):
        self._sector = sector or None
        self.invalidateFilter()

    def set_text(self, text):
        self._text = str(text or "").strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        if model is None:
            return True
        candidate = model.candidate_at(row)
        if candidate is None:
            return True

        if self._sector and (candidate.get("sector") or "") != self._sector:
            return False

        if self._text:
            haystack = " ".join(str(candidate.get(key) or "") for key in
                                ("ticker", "name", "sector", "rating")).lower()
            if self._text not in haystack:
                return False
        return True


class ScoreBarDelegate(QStyledItemDelegate):
    """The score column: the number, then a bar behind it.

    Painted rather than built from widgets because a widget per row costs more
    than it is worth in a table that re-sorts on every slider move.
    """

    BAR_WIDTH = 46
    BAR_HEIGHT = 5

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        value = index.data(BAR_ROLE)
        if value is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        rect = option.rect
        width = min(self.BAR_WIDTH, max(0, rect.width() - 46))
        if width <= 4:
            painter.restore()
            return

        x = rect.right() - width - 4
        y = rect.center().y() + 7
        painter.fillRect(x, y, width, self.BAR_HEIGHT, QColor(theme.BG))
        filled = int(round(width * max(0.0, min(10.0, float(value))) / 10.0))
        if filled:
            painter.fillRect(x, y, filled, self.BAR_HEIGHT,
                             QColor(theme.score_colour(value)))
        painter.restore()

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(max(hint.height(), 26))
        return hint


class CandidatesView(QWidget):
    """The table, its filter row and its "nothing yet" state."""

    selected = pyqtSignal(str)          # a ticker
    selection_changed = pyqtSignal()    # the checkbox-equivalent set changed
    sector_cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ── filter row ──────────────────────────────────────────────
        self._filter_row = QHBoxLayout()
        self._filter_row.setSpacing(6)

        self.sector_chip = Chip("")
        self.sector_chip.setToolTip("click to clear the sector filter")
        self.sector_chip.clicked.connect(self.sector_cleared.emit)
        self.sector_chip.hide()
        self._filter_row.addWidget(self.sector_chip)

        self.count_label = QLabel("")
        self.count_label.setObjectName("caption")
        self._filter_row.addWidget(self.count_label)
        self._filter_row.addStretch(1)
        layout.addLayout(self._filter_row)

        # ── table ───────────────────────────────────────────────────
        self.model = CandidatesModel(self)
        self.proxy = CandidatesProxy(self)
        self.proxy.setSourceModel(self.model)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_SCORE, Qt.SortOrder.DescendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setItemDelegateForColumn(COL_SCORE, ScoreBarDelegate(self.table))
        self.table.setWordWrap(False)

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_GUIDE, QHeaderView.ResizeMode.Stretch)
        for column, width in ((COL_TICKER, 96), (COL_SCORE, 104), (COL_LIQ, 64),
                              (COL_TREND, 70), (COL_PATTERN, 96), (COL_SENT, 48),
                              (COL_CUT, 62)):
            self.table.setColumnWidth(column, width)

        self.table.selectionModel().selectionChanged.connect(self._on_selection)
        layout.addWidget(self.table, 1)

        self.message = Message("")
        self.message.hide()
        layout.addWidget(self.message)

    # ── data in ─────────────────────────────────────────────────────
    def set_scan(self, scan):
        self.model.set_scan(scan)
        if scan is not None and not scan.ok:
            self.message.setText(scan.empty_reason)
            self.message.show()
            self.table.hide()
        else:
            self.message.hide()
            self.table.show()
        self._sync_count()

    def set_sizing(self, sizing_by_ticker):
        self.model.set_sizing(sizing_by_ticker)

    def set_sector_filter(self, sector):
        self.proxy.set_sector(sector)
        if sector:
            self.sector_chip.setText(f"filtered: {sector}   ×")
            self.sector_chip.show()
        else:
            self.sector_chip.hide()
        self._sync_count()

    def set_text_filter(self, text):
        self.proxy.set_text(text)
        self._sync_count()

    def _sync_count(self):
        shown = self.proxy.rowCount()
        total = self.model.rowCount()
        if shown == total:
            self.count_label.setText(f"{total} candidate(s)")
        else:
            self.count_label.setText(f"{shown} of {total} candidate(s)")

    # ── selection ───────────────────────────────────────────────────
    def selected_tickers(self) -> list:
        """Every ticker in the current row selection, in table order."""
        out = []
        for index in self.table.selectionModel().selectedRows():
            ticker = index.data(TICKER_ROLE)
            if ticker and ticker not in out:
                out.append(ticker)
        return out

    def _on_selection(self, *_args):
        tickers = self.selected_tickers()
        if tickers:
            self.selected.emit(tickers[0])
        self.selection_changed.emit()

    def select_ticker(self, ticker):
        """Select a row by ticker, scrolling it into view."""
        for row in range(self.proxy.rowCount()):
            index = self.proxy.index(row, COL_TICKER)
            if index.data(TICKER_ROLE) == ticker:
                self.table.selectRow(row)
                self.table.scrollTo(index)
                return True
        return False

    # ── view state, so a reload does not lose the person's place ───
    def view_state(self) -> dict:
        header = self.table.horizontalHeader()
        return {
            "selected": self.selected_tickers(),
            "scroll": self.table.verticalScrollBar().value(),
            "sort_column": header.sortIndicatorSection(),
            "sort_order": int(header.sortIndicatorOrder().value),
        }

    def restore_view_state(self, state):
        if not isinstance(state, dict):
            return
        column = state.get("sort_column")
        order = state.get("sort_order")
        if isinstance(column, int) and isinstance(order, int):
            self.table.sortByColumn(column, Qt.SortOrder(order))

        selection = self.table.selectionModel()
        selection.blockSignals(True)
        for ticker in (state.get("selected") or []):
            self.select_ticker(ticker)
        selection.blockSignals(False)

        scroll = state.get("scroll")
        if isinstance(scroll, int):
            self.table.verticalScrollBar().setValue(scroll)
