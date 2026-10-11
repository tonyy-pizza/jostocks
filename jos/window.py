r"""jos/window.py - the window, and the wiring between the panels.

Layout, top to bottom (section 6):

    toolbar        refresh, freshness text, status chip
    sizing strip   always visible, native
    row 1          ratings (dropdown: candidates / changes / movers /
                   holdings) and correlation (dropdown: heatmap / scatter /
                   clusters)
    row 2          sector ratings, ticker detail, portfolio fit
    row 3          calendar, full width

Phase 1 builds the panels marked in section 11 and leaves the Phase 2 and 3
views as named placeholders inside their dropdowns, rather than hiding them -
so the shape of the finished window is visible and the dropdown positions do
not move when they arrive.

Three behaviours here are worth reading before changing anything:

  - A reload must not lose the person's place. `_reload` captures the table's
    selection, scroll and sort, swaps the Scan, then restores them. That is
    section 7's requirement and the reason the candidate table swaps data
    rather than being rebuilt.
  - The sliders drive three panels. Every sizing change recomputes once, into
    one dict, which is then handed to the table, the scatter and the
    portfolio fit. Recomputing per panel would be three times the work for
    the same answer.
  - Wake detection lives here. Qt timers do not fire while the machine is
    suspended, so a gap between one-second ticks that is far larger than one
    second means time passed without us running. It catches hibernate and a
    corrected clock for free, and it is the mechanism this project uses
    instead of a scheduled task.
"""

from __future__ import annotations

import datetime as dt

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QPushButton,
                             QScrollArea, QSizePolicy, QSplitter, QVBoxLayout,
                             QWidget)

from . import changes as changes_module
from . import corr as corr_module
from . import earnings as earnings_module
from . import health as health_module
from . import pipeline as pipe
from . import sizing as sizing_module
from . import state as state_module
from . import theme, workers
from .datastore import DataWatcher, Scan, describe_age
from .paths import DataPaths, pipeline_data_dir
from .refresh import RefreshController
from .views.candidates import CandidatesView
from .views.changes import ChangesView
from .views.charts import (clusters_html, currency_split, empty, heatmap,
                           portfolio_fit, score_vs_correlation)
from .views.holdings import HoldingsView
from .views.panel import DropdownPanel, Message, Panel
from .views.sectors import SectorsView
from .views.sizing_strip import SizingStrip
from .views.status import StatusChip
from .views.ticker_detail import SPARK_WEEKS, TickerDetail
from .views.webpanel import AVAILABLE as WEBVIEW_AVAILABLE
from .views.webpanel import WebPanelBody

#: A clock jump larger than this between one-second ticks means the machine
#: was suspended. Dionysus uses 90 seconds for the same test.
WAKE_GAP_SECONDS = 90

#: The window must be usable at this width (section 6).
MIN_WIDTH = 1280
MIN_HEIGHT = 720

RATINGS_VIEWS = (("candidates", "candidates"),
                 ("changes", "changes since last scan"),
                 ("movers", "popular & movers"),
                 ("holdings", "holdings & health"))

CORRELATION_VIEWS = (("heatmap", "correlation heatmap"),
                     ("scatter", "score vs correlation"),
                     ("clusters", "clusters"))

CALENDAR_VIEWS = (("earnings", "earnings dates"),
                  ("macro", "macro releases"))

PHASE_NOTE = {
    "movers": ("Phase 3. Market movers and most-active names, plus a "
               "watchlist for names the screener misses."),
    "earnings": ("Phase 3. A two-week strip with report days, covering held "
                 "names and the displayed candidates."),
    "macro": ("Phase 3. Fed and Bank of Canada decisions, jobs, CPI, PCE, "
              "GDP and retail sales, refreshed weekly by macro_refresh.py."),
}


class MainWindow(QMainWindow):
    """The jostocks window."""

    def __init__(self, settings, notes, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.notes = notes

        self.scan = None
        self._sizing = {}
        self._selection = []
        self._matrix = None
        self._matrix_pending = False
        self._last_tick = None
        self._sector_filter = None
        self._rescore_pending = False
        self._earnings_pending = False

        self.data_paths = DataPaths(pipeline_data_dir(settings.get("data_dir")))

        self.setWindowTitle("jostocks")
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

        self._build()
        self._wire()

        self.refresh = RefreshController(settings, self.data_paths, self)
        self.refresh.status_changed.connect(self._on_status)
        self.refresh.finished.connect(self._on_refresh_finished)

        self.watcher = DataWatcher(self.data_paths, parent=self)
        self.watcher.changed.connect(self._reload)

        # The wake detector. One second is frequent enough to notice a jump
        # and cheap enough to ignore.
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        self._clock.start()

        self._restore_geometry()
        self._reload(first=True)

    # ── construction ────────────────────────────────────────────────
    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        # Everything below the toolbar scrolls, so a short window still
        # reaches the bottom row (section 6).
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(10, 10, 10, 10)
        self._body_layout.setSpacing(10)

        self.sizing_strip = SizingStrip()
        self._body_layout.addWidget(self.sizing_strip)

        self._body_layout.addWidget(self._build_row_one(), 3)
        self._body_layout.addWidget(self._build_row_two(), 3)
        self._body_layout.addWidget(self._build_row_three(), 1)

        scroller.setWidget(body)
        root.addWidget(scroller, 1)

    def _build_toolbar(self):
        bar = QWidget()
        bar.setObjectName("toolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(10)

        self.refresh_button = QPushButton("refresh")
        self.refresh_button.setToolTip(
            "Re-read the data from disk, and run a scan if the data is stale. "
            "A manual refresh ignores the minimum interval between scans.")
        layout.addWidget(self.refresh_button)

        self.freshness = QLabel("")
        self.freshness.setObjectName("caption")
        layout.addWidget(self.freshness)

        layout.addStretch(1)

        self.selection_label = QLabel("0 selected")
        self.selection_label.setObjectName("caption")
        self.selection_label.setToolTip(
            "the shared selection: candidates ticked here feed the scatter "
            "and the portfolio-fit panel")
        layout.addWidget(self.selection_label)

        self.status_chip = StatusChip()
        layout.addWidget(self.status_chip)
        return bar

    def _build_row_one(self):
        row = QSplitter(Qt.Orientation.Horizontal)
        row.setChildrenCollapsible(False)
        row.setHandleWidth(10)

        # ── ratings ─────────────────────────────────────────────────
        self.ratings_panel = DropdownPanel(RATINGS_VIEWS, tag="native")
        self.ratings_stack = _Stack()

        self.candidates = CandidatesView()
        self.ratings_stack.add("candidates", self.candidates)
        self.changes = ChangesView()
        self.ratings_stack.add("changes", self.changes)
        self.ratings_stack.add("movers", Message(PHASE_NOTE["movers"]))
        self.holdings = HoldingsView(settings=self.settings)
        self.ratings_stack.add("holdings", self.holdings)

        self.ratings_panel.set_body(self.ratings_stack)
        row.addWidget(self.ratings_panel)

        # ── correlation ─────────────────────────────────────────────
        self.correlation_panel = DropdownPanel(CORRELATION_VIEWS, tag="web")
        correlation_body = QWidget()
        correlation_layout = QVBoxLayout(correlation_body)
        correlation_layout.setContentsMargins(0, 0, 0, 0)
        correlation_layout.setSpacing(4)

        self.correlation_warning = QLabel("")
        self.correlation_warning.setObjectName("caption")
        self.correlation_warning.setStyleSheet(f"color: {theme.AMBER};")
        self.correlation_warning.setWordWrap(True)
        self.correlation_warning.hide()
        correlation_layout.addWidget(self.correlation_warning)

        self.correlation_web = WebPanelBody()
        correlation_layout.addWidget(self.correlation_web, 1)

        self.correlation_footer = QLabel("")
        self.correlation_footer.setObjectName("faint")
        self.correlation_footer.setWordWrap(True)
        correlation_layout.addWidget(self.correlation_footer)

        self.correlation_panel.set_body(correlation_body)
        row.addWidget(self.correlation_panel)

        row.setSizes([760, 520])
        return row

    def _build_row_two(self):
        row = QSplitter(Qt.Orientation.Horizontal)
        row.setChildrenCollapsible(False)
        row.setHandleWidth(10)

        self.sector_panel = Panel("sector ratings", tag="native")
        self.sectors = SectorsView()
        self.sector_panel.set_body(self.sectors)
        self.sector_panel.setMinimumWidth(190)
        row.addWidget(self.sector_panel)

        self.detail_panel = Panel("ticker detail", tag="native")
        self.detail = TickerDetail(notes=self.notes)
        self.detail_panel.set_body(self.detail)
        row.addWidget(self.detail_panel)

        self.fit_panel = Panel("portfolio fit", tag="web")
        fit_body = QWidget()
        fit_layout = QVBoxLayout(fit_body)
        fit_layout.setContentsMargins(0, 0, 0, 0)
        fit_layout.setSpacing(4)
        self.fit_web = WebPanelBody()
        fit_layout.addWidget(self.fit_web, 1)
        self.currency_web = WebPanelBody()
        self.currency_web.setMaximumHeight(104)
        fit_layout.addWidget(self.currency_web)
        self.fit_note = QLabel("")
        self.fit_note.setObjectName("faint")
        self.fit_note.setWordWrap(True)
        fit_layout.addWidget(self.fit_note)
        self.fit_panel.set_body(fit_body)
        row.addWidget(self.fit_panel)

        row.setSizes([230, 580, 470])
        return row

    def _build_row_three(self):
        self.calendar_panel = DropdownPanel(CALENDAR_VIEWS, tag="native")
        self.calendar_stack = _Stack()
        self.calendar_stack.add("earnings", Message(PHASE_NOTE["earnings"]))
        self.calendar_stack.add("macro", Message(PHASE_NOTE["macro"]))
        self.calendar_panel.set_body(self.calendar_stack)
        self.calendar_panel.setMinimumHeight(130)
        self.calendar_panel.setSizePolicy(QSizePolicy.Policy.Expanding,
                                          QSizePolicy.Policy.Preferred)
        return self.calendar_panel

    # ── wiring ──────────────────────────────────────────────────────
    def _wire(self):
        self.refresh_button.clicked.connect(self._on_refresh_clicked)

        self.sizing_strip.changed.connect(self._apply_sizing)
        self.sizing_strip.changed_now.connect(self._apply_sizing)

        self.candidates.selected.connect(self._on_candidate_selected)
        self.candidates.selection_changed.connect(self._on_selection_changed)
        self.candidates.sector_cleared.connect(lambda: self._set_sector(None))

        self.holdings.selected.connect(
            lambda ticker: self.detail.show_ticker(ticker,
                                                   source="selected in holdings"))
        self.holdings.rescore_requested.connect(self._rescore_holdings)

        self.changes.selected.connect(
            lambda ticker: self.detail.show_ticker(
                ticker, source="selected in changes"))

        self.sectors.sector_clicked.connect(self._on_sector_clicked)

        self.detail.lookup_requested.connect(self._on_lookup)

        self.detail.add_to_selection.connect(self._add_to_selection)
        self.detail.reevaluate_requested.connect(self._reevaluate)

        self.ratings_panel.view_changed.connect(self._on_ratings_view)
        self.correlation_panel.view_changed.connect(self._on_correlation_view)
        self.calendar_panel.view_changed.connect(self._on_calendar_view)

        # Ctrl+R, which Qt maps to Cmd+R on macOS. Section 9b: no
        # hardcoded Windows-only shortcuts.
        QShortcut(QKeySequence(QKeySequence.StandardKey.Refresh), self,
                  self._on_refresh_clicked)
        QShortcut(QKeySequence("Ctrl+R"), self, self._on_refresh_clicked)

        # Remembered views (section 6).
        self.ratings_panel.set_view(self.settings.view("ratings"), emit=False)
        self.correlation_panel.set_view(self.settings.view("correlation"),
                                        emit=False)
        self.calendar_panel.set_view(self.settings.view("calendar"), emit=False)
        self.ratings_stack.show_only(self.ratings_panel.current_view())
        self.calendar_stack.show_only(self.calendar_panel.current_view())

    # ── loading ─────────────────────────────────────────────────────
    def _reload(self, first=False):
        """Re-read the data, keeping the person's place (section 7)."""
        table_state = None if first else self.candidates.view_state()
        subject = self.detail.subject()

        self.scan = Scan(self.data_paths)

        if first:
            self.sizing_strip.apply_defaults(self.scan.sizing_defaults(),
                                             self.settings.get("sizing"))

        self.candidates.set_scan(self.scan)
        self.detail.set_scan(self.scan)
        self.detail.set_recent(self.settings.get("recent_lookups") or [])
        self.sectors.set_scan(self.scan)
        self.sectors.set_active(self._sector_filter)

        self._refresh_history_views()
        self._apply_sizing()

        if table_state:
            self.candidates.restore_view_state(table_state)
        if subject:
            self.detail.show_ticker(subject)
        elif first and self.scan.candidates:
            self.candidates.select_ticker(self.scan.candidates[0].get("ticker"))

        self._sync_toolbar()
        self._refresh_correlation(force=False)
        self._refresh_fit()

        # Section 8: file a history snapshot when a scan produced new data.
        # Read the previous snapshot BEFORE filing this one, or "changes since
        # last scan" would compare the scan against a copy of itself.
        if self.scan.sized:
            state_module.take_snapshot(
                self.scan.sized,
                keep_days=self.settings.get("history_keep_days", 90))

        # A sizing wrapper that has drifted from position_sizer is worth
        # saying out loud rather than quietly showing different numbers.
        problems = sizing_module.verify(self.scan.candidates, self.scan.params)
        if problems:
            worst = problems[0]
            self.freshness.setToolTip(
                f"warning: the app's sizing disagrees with the scan for "
                f"{len(problems)} name(s); worst is {worst['ticker']} "
                f"({worst['stored']} on file vs {worst['recomputed']} "
                f"recomputed). jos/sizing.py needs to follow a change in "
                f"position_sizer.py.")

    def _sync_toolbar(self):
        if self.scan is None:
            return
        self.freshness.setText(self.scan.freshness_line())
        self.status_chip.update_state(
            self.scan, self.refresh.status(),
            stale_days=self.settings.get("stale_warning_days", 3),
            wake_note=self._wake_note())

    def _wake_note(self):
        """The honest version of what wake does, for the chip's tooltip."""
        if not self.settings.get("auto_refresh_on_wake", True):
            return "wake refresh is off (auto_refresh_on_wake)"
        return ("Wake is detected in the app, so a refresh after sleep only "
                "happens while this window is open. With it closed, the next "
                "logon refreshes.")

    # ── history-backed views ────────────────────────────────────────
    def _refresh_history_views(self):
        """Feed the changes table and the holdings baseline.

        Both read data/history/. The previous snapshot is read before this
        scan is filed, which is why _reload calls this ahead of
        take_snapshot: otherwise the newest snapshot IS this scan and every
        comparison comes back empty.
        """
        # The snapshot of THIS scan is skipped: the app files one for every
        # scan it loads, so on the second draw the newest snapshot is the
        # scan on screen and the diff would always be empty.
        _path, previous = state_module.previous_snapshot(self.scan.generated_at)
        self.changes.set_result(changes_module.compare(
            self.scan.sized, previous, scored=self.scan.scored))

        limits = health_module.thresholds(self.settings)
        _path, baseline_doc = state_module.snapshot_nearest(
            limits["lookback_days"])
        self.holdings.set_scan(
            self.scan,
            history_days=state_module.history_days(),
            baseline=health_module.baseline_from(baseline_doc))

        # A cached re-score survives a restart, so the health column is not
        # blank until someone presses the button.
        cached = state_module.read_holdings_scored()
        if cached:
            self.holdings.set_rescored(cached)

    # ── sector filter ───────────────────────────────────────────────
    def _on_sector_clicked(self, sector):
        """Clicking a sector filters; clicking it again clears (section 6)."""
        self._set_sector(None if sector == self._sector_filter else sector)

    def _set_sector(self, sector):
        self._sector_filter = sector or None
        self.candidates.set_sector_filter(self._sector_filter)
        self.sectors.set_active(self._sector_filter)
        if self._sector_filter:
            # Section 6: switch the ratings dropdown to candidates if it is on
            # another view, so the filter you just applied is visible.
            self.ratings_panel.set_view("candidates")

    # ── lookup ──────────────────────────────────────────────────────
    def _on_lookup(self, symbol):
        """A symbol typed into ticker detail, or a chip clicked.

        Three outcomes, in the order they are cheap:
          - already in the scan: show it at once, no network
          - resolves to one listing: score it in a worker
          - resolves to several: ask which listing was meant
        """
        ticker = str(symbol or "").strip().upper()
        if not ticker:
            return

        self.settings.remember_lookup(ticker)
        self.detail.set_recent(self.settings.get("recent_lookups") or [])

        if self.scan is not None and (ticker in self.scan.by_ticker
                                      or ticker in self.scan.scored_by_ticker):
            age = describe_age(self.scan.age)
            self.detail.set_lookup_state("")
            self.detail.show_ticker(ticker,
                                    source=f"in pipeline - scored {age}")
            self._load_sparkline(ticker)
            return

        if not pipe.ready():
            self.detail.set_lookup_state(
                "looking up a new symbol needs the stocks pipeline, which "
                "could not be imported", level="warn")
            return

        self.detail.set_lookup_state(f"resolving {ticker}...", level="busy")
        workers.submit(workers.resolve_symbol, ticker,
                       on_done=lambda found, t=ticker: self._on_resolved(t, found),
                       on_failed=lambda why: self.detail.set_lookup_state(
                           f"could not look that up: {why}", level="warn"),
                       on_progress=lambda text: self.detail.set_lookup_state(
                           text, level="busy"))

    def _on_resolved(self, requested, found):
        """What resolve_symbol came back with."""
        if not found:
            self.detail.set_lookup_state(
                f"no listing found for {requested}. Check the symbol, or add "
                f"the exchange suffix (.TO, .V, .CN, .NE).", level="warn")
            return

        if len(found) > 1:
            self.detail.set_lookup_state(
                f"{requested} matches {len(found)} listings", level="warn")
            self.detail.show_listing_choice(found)
            return

        ticker = found[0].get("ticker") or requested
        self.detail.set_lookup_state(f"evaluating {ticker}...", level="busy")
        workers.submit(workers.score_ticker, ticker,
                       on_done=lambda result, t=ticker: self._on_scored(t, result),
                       on_failed=lambda why: self.detail.set_lookup_state(
                           f"could not evaluate: {why}", level="warn"))

    def _on_scored(self, ticker, result):
        """A looked-up ticker, scored through the evaluator's own path."""
        record, reason = result if isinstance(result, tuple) else (None, None)
        if record is None:
            self.detail.set_lookup_state(
                f"{ticker} could not be evaluated: {reason or 'no data'}",
                level="warn")
            return

        self.detail.set_lookup_state("")
        self.detail.remember_external(ticker, record)
        self.detail.show_ticker(ticker,
                                source="looked up - evaluated just now")
        self._load_sparkline(ticker)

    # ── holdings re-score ───────────────────────────────────────────
    def _rescore_holdings(self):
        """Run every holding through stage 1 again, off the UI thread."""
        if self.scan is None or self._rescore_pending:
            return
        holdings = self.scan.holdings
        if not holdings:
            return
        if not pipe.ready():
            self.holdings.footer.setText(
                "re-scoring needs the stocks pipeline, which could not be "
                "imported")
            return

        self._rescore_pending = True
        self.holdings.rescore_chip.setEnabled(False)
        self.holdings.footer.setText(
            f"re-scoring {len(holdings)} holding(s)...")

        tickers = [h.get("ticker") for h in holdings if h.get("ticker")]

        def _score_all(progress=None):
            records = {}
            for index, ticker in enumerate(tickers, start=1):
                if progress:
                    progress(f"re-scoring {ticker} "
                             f"({index} of {len(tickers)})...")
                record, _reason = workers.score_ticker(ticker,
                                                       force_refresh=True)
                if record:
                    records[ticker] = record
            return records

        workers.submit(_score_all,
                       on_done=self._on_rescored,
                       on_failed=self._on_rescore_failed,
                       on_progress=lambda text:
                           self.holdings.footer.setText(text))

        self._fetch_earnings(tickers)

    def _on_rescored(self, records):
        self._rescore_pending = False
        self.holdings.rescore_chip.setEnabled(True)
        if records:
            state_module.write_holdings_scored(records)
            self.holdings.set_rescored(records)
        else:
            self.holdings.footer.setText(
                "no holding could be re-scored - check the network")

    def _on_rescore_failed(self, reason):
        self._rescore_pending = False
        self.holdings.rescore_chip.setEnabled(True)
        self.holdings.footer.setText(f"re-score failed: {reason}")

    def _fetch_earnings(self, tickers):
        """Next report dates for the holdings, cached and fail-soft."""
        if self._earnings_pending or not tickers or not pipe.ready():
            return
        self._earnings_pending = True

        def _done(dates):
            self._earnings_pending = False
            self.holdings.set_earnings(dates)

        def _failed(_reason):
            self._earnings_pending = False

        workers.submit(earnings_module.next_reports, tickers,
                       on_done=_done, on_failed=_failed)

    # ── sizing ──────────────────────────────────────────────────────
    def _apply_sizing(self):
        if self.scan is None:
            return
        values = self.sizing_strip.values()
        self._sizing = sizing_module.recompute_all(
            self.scan.candidates, values["threshold"],
            values["reduction_factor"], basis=values["basis"],
            flat=values["flat"])

        self.candidates.set_sizing(self._sizing)
        self.detail.set_sizing(self._sizing)

        cut = sum(1 for s in self._sizing.values()
                  if s.get("correlation_adjusted"))
        held = sum(1 for c in self.scan.candidates if c.get("already_held"))
        self.sizing_strip.set_summary(
            f"{cut} cut · {held} held · "
            f"{len(self.scan.candidates)} sized")

        self.settings.set("sizing", values)

        self._draw_correlation()
        self._refresh_fit()

    # ── correlation panel ───────────────────────────────────────────
    def _on_correlation_view(self, key):
        self.settings.set_view("correlation", key)
        self._draw_correlation()
        if key == "heatmap":
            self._refresh_correlation(force=False)

    def _refresh_correlation(self, force=False):
        """Compute the matrix in a worker, if the heatmap needs it."""
        if self.scan is None or not self.scan.ok:
            return
        if not WEBVIEW_AVAILABLE:
            return
        if self._matrix_pending:
            return

        key = corr_module.cache_key(
            list(self.scan.by_ticker) + self.scan.holding_tickers,
            self.scan.generated_at)
        if not force and self._matrix is not None and \
                self._matrix.get("key") == key:
            return

        cached = corr_module.load_cached(key)
        if cached is not None and not force:
            self._matrix = cached
            self._draw_correlation()
            return

        if not pipe.ready():
            self.correlation_web.set_busy(
                "the correlation matrix needs the stocks pipeline, which "
                "could not be imported")
            return

        self._matrix_pending = True
        if self.correlation_panel.current_view() == "heatmap":
            self.correlation_web.set_busy("computing correlations...")

        workers.submit(
            corr_module.matrix_for, self.scan, force_refresh=force,
            on_done=self._on_matrix,
            on_failed=self._on_matrix_failed,
            on_progress=lambda text: self.correlation_web.set_busy(text))

    def _on_matrix(self, matrix):
        self._matrix_pending = False
        self._matrix = matrix
        self._draw_correlation()

    def _on_matrix_failed(self, reason):
        self._matrix_pending = False
        self.correlation_web.set_busy(f"could not build the matrix: {reason}")

    def _draw_correlation(self):
        if self.scan is None:
            return
        view = self.correlation_panel.current_view()
        values = self.sizing_strip.values()

        if not WEBVIEW_AVAILABLE:
            self.correlation_footer.setText(
                "install PyQt6-WebEngine to draw this panel")
            return

        if view == "clusters":
            self.correlation_web.render_html(clusters_html(self.scan))
            self.correlation_warning.hide()
            counts = (f"{len(self.scan.clusters)} cluster(s), "
                      f"{len(self.scan.standalone)} standalone")
            self.correlation_footer.setText(counts)
            return

        if view == "scatter":
            self.correlation_web.render(score_vs_correlation(
                self.scan.candidates, self._sizing, values["threshold"],
                basis=values["basis"]))
            self.correlation_warning.hide()
            self.correlation_footer.setText(
                f"x is the largest {values['basis']} correlation to any "
                f"holding; the dashed line is the current threshold")
            return

        # heatmap
        if self._matrix is None:
            if self._matrix_pending:
                self.correlation_web.set_busy("computing correlations...")
            else:
                self.correlation_web.render(empty(
                    "no correlation matrix yet"))
            return

        order = corr_module.ordered_tickers(self.scan, self._matrix)
        blocks = corr_module.cluster_blocks(self.scan, order)
        self.correlation_web.render(heatmap(
            self._matrix, order, self.scan.holding_tickers,
            values["threshold"], basis="cleaned", blocks=blocks,
            scan=self.scan))

        # Section 6: the T/N warning in amber above the map, and a visible
        # warning when the recomputed values disagree with the sizing.
        notes = []
        warning = self.scan.correlation_warning() or \
            (self._matrix.get("meta") or {}).get("warning")
        if warning:
            notes.append(str(warning))

        mismatches = self._matrix.get("mismatches") or []
        if mismatches:
            worst = mismatches[0]
            notes.append(
                f"{len(mismatches)} recomputed correlation(s) disagree with "
                f"the sizing by more than "
                f"{corr_module.SELF_CHECK_TOLERANCE}: worst is "
                f"{worst['pair']} {worst['basis']} "
                f"{worst['stored']} on file vs {worst['recomputed']} here. "
                f"The map and the sizing were not built from the same panel.")

        if notes:
            self.correlation_warning.setText("  ".join(notes))
            self.correlation_warning.show()
        else:
            self.correlation_warning.hide()

        footer = self.scan.correlation_footer()
        excluded = self.scan.correlation_exclusions()
        if excluded:
            sample = ", ".join(f"{t} ({why})" for t, why in
                               list(excluded.items())[:2])
            footer += f" · excluded: {sample}"
            if len(excluded) > 2:
                footer += f" and {len(excluded) - 2} more"
        worst_delta = self._matrix.get("max_mismatch")
        if worst_delta is not None and not mismatches:
            footer += (f" · self-check passed, largest difference from "
                       f"the stored pairs {worst_delta}")
        self.correlation_footer.setText(footer)

    # ── portfolio fit ───────────────────────────────────────────────
    def _refresh_fit(self):
        if self.scan is None or not WEBVIEW_AVAILABLE:
            return
        self.fit_web.render(portfolio_fit(self.scan, self._selection,
                                          self._sizing, use_high=False))
        self.currency_web.render(currency_split(self.scan))
        note = ("bars use the ADJUSTED midpoint of each selected candidate's "
                "guidance")
        if self.scan.mixed_currency:
            note += " · holdings settle in more than one currency and " \
                    "are never summed across them"
        self.fit_note.setText(note)

    # ── selection ───────────────────────────────────────────────────
    def _on_candidate_selected(self, ticker):
        self.detail.show_ticker(ticker, source="selected in candidates")
        self._load_sparkline(ticker)

    def _on_selection_changed(self):
        self._selection = self.candidates.selected_tickers()
        self.selection_label.setText(f"{len(self._selection)} selected")
        self._refresh_fit()

    def _add_to_selection(self, ticker):
        if ticker and ticker not in self._selection:
            self._selection.append(ticker)
            self.candidates.select_ticker(ticker)
            self.selection_label.setText(f"{len(self._selection)} selected")
            self._refresh_fit()

    # ── ticker detail helpers ───────────────────────────────────────
    def _load_sparkline(self, ticker):
        if not pipe.ready():
            self.detail.set_sparkline([], "price history needs the pipeline")
            return
        self.detail.set_sparkline([], "loading price history...")
        subject = ticker

        def _done(rows):
            # The subject may have changed while this was in flight.
            if self.detail.subject() == subject:
                self.detail.set_sparkline(rows)

        def _failed(reason):
            if self.detail.subject() == subject:
                self.detail.set_sparkline([], f"no price history ({reason})")

        workers.submit(workers.price_history, ticker, period="6mo",
                       on_done=_done, on_failed=_failed)

    def _reevaluate(self, ticker):
        if not pipe.ready():
            return
        self.detail.set_sparkline([], f"re-evaluating {ticker}...")

        def _done(result):
            record, reason = result if isinstance(result, tuple) else (None, None)
            if record is None:
                self.detail.set_sparkline([], f"could not re-evaluate: {reason}")
                return
            self.scan.scored_by_ticker[ticker] = record
            self.detail.show_ticker(ticker, source="re-evaluated just now")
            self._load_sparkline(ticker)

        workers.submit(workers.score_ticker, ticker, force_refresh=True,
                       on_done=_done,
                       on_failed=lambda why: self.detail.set_sparkline(
                           [], f"could not re-evaluate: {why}"))

    # ── dropdown views ──────────────────────────────────────────────
    def _on_ratings_view(self, key):
        self.settings.set_view("ratings", key)
        self.ratings_stack.show_only(key)

    def _on_calendar_view(self, key):
        self.settings.set_view("calendar", key)
        self.calendar_stack.show_only(key)

    # ── refresh ─────────────────────────────────────────────────────
    def _on_refresh_clicked(self):
        self.watcher.poke()
        self._reload()
        self.refresh.start(reason="manual", manual=True)

    def _on_status(self, _status):
        self._sync_toolbar()

    def _on_refresh_finished(self, ok, message):
        self._sync_toolbar()
        if ok:
            self.watcher.poke()

    # ── wake detection ──────────────────────────────────────────────
    def _tick(self):
        now = dt.datetime.now()
        previous, self._last_tick = self._last_tick, now
        if previous is None:
            return
        gap = (now - previous).total_seconds()
        if gap < WAKE_GAP_SECONDS:
            return

        # Timers do not run while the machine is suspended, so a gap this
        # large means time passed without us. Catches hibernate and a
        # corrected system clock too, both of which also leave stale data on
        # screen.
        self._reload()
        if self.settings.get("auto_refresh_on_wake", True):
            self.refresh.start(reason="wake")

    # ── geometry ────────────────────────────────────────────────────
    def _restore_geometry(self):
        geometry = self.settings.get("window_geometry")
        if isinstance(geometry, (list, tuple)) and len(geometry) == 4:
            try:
                self.setGeometry(*[int(v) for v in geometry])
            except (TypeError, ValueError):
                self.resize(MIN_WIDTH, 900)
        else:
            self.resize(MIN_WIDTH, 900)
        if self.settings.get("window_maximised"):
            self.showMaximized()

    def closeEvent(self, event):
        self.detail.flush_notes()
        # Stop in-flight jobs reporting into widgets that are about to go.
        workers.shutdown()
        self._clock.stop()
        if not self.isMaximized():
            rect = self.geometry()
            self.settings.set("window_geometry",
                              [rect.x(), rect.y(), rect.width(), rect.height()])
        self.settings.set("window_maximised", self.isMaximized())
        self.settings.save()
        super().closeEvent(event)

    # ── the single-instance hook ────────────────────────────────────
    def handle_message(self, message):
        """A message from a second launch, or from the autorun."""
        text = str(message or "").strip()
        if text == "refresh-now":
            self.refresh.start(reason="autorun", manual=True)
        elif text == "refresh-finished":
            self.watcher.poke()
            self._reload()
        self.show()
        self.raise_()
        self.activateWindow()


class _Stack(QWidget):
    """A plain stack of named widgets, all built once.

    Not QStackedWidget: that sizes itself to the largest child, which with a
    table in one slot and a one-line message in another leaves the panel
    stretched to the table's height whatever is showing.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._widgets = {}

    def add(self, key, widget):
        self._widgets[key] = widget
        self._layout.addWidget(widget, 1)
        widget.hide()
        return widget

    def show_only(self, key):
        for name, widget in self._widgets.items():
            widget.setVisible(name == key)

    def widget(self, key):
        return self._widgets.get(key)
