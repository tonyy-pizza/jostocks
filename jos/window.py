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

from . import corr as corr_module
from . import pipeline as pipe
from . import sizing as sizing_module
from . import state as state_module
from . import theme, workers
from .datastore import DataWatcher, Scan, describe_age
from .paths import DataPaths, pipeline_data_dir
from .refresh import RefreshController
from .views.candidates import CandidatesView
from .views.charts import (clusters_html, currency_split, empty, heatmap,
                           portfolio_fit, score_vs_correlation)
from .views.holdings import HoldingsView
from .views.panel import DropdownPanel, Message, Panel
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
    "changes": ("Phase 2. Compares the latest scan against the previous "
                "snapshot in data/history/ and lists what is new, up, down "
                "or dropped, with a short reason for each."),
    "movers": ("Phase 3. Market movers and most-active names, plus a "
               "watchlist for names the screener misses."),
    "earnings": ("Phase 3. A two-week strip with report days, covering held "
                 "names and the displayed candidates."),
    "macro": ("Phase 3. Fed and Bank of Canada decisions, jobs, CPI, PCE, "
              "GDP and retail sales, refreshed weekly by macro_refresh.py."),
    "sectors": ("Phase 2. Groups the scored candidates by sector with an "
                "average rating, a count and the best name, and filters the "
                "candidates table when a sector is clicked."),
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
        self.ratings_stack.add("changes", Message(PHASE_NOTE["changes"]))
        self.ratings_stack.add("movers", Message(PHASE_NOTE["movers"]))
        self.holdings = HoldingsView()
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
        self.sector_panel.set_body(Message(PHASE_NOTE["sectors"]))
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
        self.candidates.sector_cleared.connect(
            lambda: self.candidates.set_sector_filter(None))

        self.holdings.selected.connect(
            lambda ticker: self.detail.show_ticker(ticker,
                                                   source="selected in holdings"))

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
        self.holdings.set_scan(self.scan)
        self.detail.set_scan(self.scan)

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
