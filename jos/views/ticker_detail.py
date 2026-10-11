r"""jos/views/ticker_detail.py - one ticker, in detail.

The panel always shows exactly one ticker, the subject. In Phase 1 the subject
arrives by selection - a row in the ratings table, or a point in a web panel.
The lookup box that lets any symbol become the subject is Phase 2, and the
space for it is left in the header rather than designed around later.

Two columns, as section 6 lays them out:

  left   name, sector, currency, composite, the dimension bars, flags, and
         the sized guidance with the holding it correlates with most
  right   Piotroski, Altman Z, ROA trend, FCF-positive years, divergence
          pattern - and deliberately NOT the composite again, which is in the
          header - plus the price sparkline and the notes box

The dimension bars use the real keys from `dims`, in the evaluator's own
order, via pipeline.dims_order(). There are six (Valuation, Profitability,
Growth, Health, Momentum, Frameworks) and hardcoding five - or inventing a
"quality" that the evaluator does not write - would mean a panel that stops
matching the scan after the next scoring change.

The sparkline is painted, not plotted: it is 70 pixels of line with no axes,
and a web view for that would cost more than it draws.
"""

from __future__ import annotations

import datetime as dt

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPlainTextEdit, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from .. import pipeline as pipe
from .. import sizing as sizing_module
from .. import theme
from ..datastore import describe_age, age_seconds, venue
from .panel import Chip, Message

#: Notes save on a debounce, so typing does not write a file per keystroke.
NOTES_SAVE_MS = 800

#: Section 6: 24 weeks of price history in the sparkline.
SPARK_WEEKS = 24


class Sparkline(QWidget):
    """A bare price line. No axes, no grid, no interaction.

    Coloured by direction over the window rather than by score: this is the
    one place in the app showing price rather than a judgement about it, and
    tinting it with the composite's palette would imply the two agree.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closes = []
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(220, 46)

    def set_closes(self, closes):
        self._closes = [c for c in (closes or []) if c is not None]
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 3, -1, -3)

        if len(self._closes) < 2:
            painter.setPen(QPen(QColor(theme.FAINT)))
            painter.setFont(theme.font("body", theme.CAPTION_PX))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft
                                       | Qt.AlignmentFlag.AlignVCenter),
                             "no price history")
            return

        low, high = min(self._closes), max(self._closes)
        span = (high - low) or 1.0
        count = len(self._closes)
        step = rect.width() / max(1, count - 1)

        path = QPainterPath()
        for index, close in enumerate(self._closes):
            x = rect.left() + index * step
            y = rect.bottom() - ((close - low) / span) * rect.height()
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        rising = self._closes[-1] >= self._closes[0]
        painter.setPen(QPen(QColor(theme.GREEN if rising else theme.RED), 1.4))
        painter.drawPath(path)


class DimensionBars(QWidget):
    """One row per dimension: name, bar, value.

    The value is printed next to every bar, so the bars are a second reading
    of a number that is also written down - not the only way to read it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(3)
        self._rows = []

    def set_dims(self, dims, coverage=None):
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self._rows = []

        for row, key in enumerate(pipe.dims_order(dims)):
            value = pipe.num((dims or {}).get(key))

            name = QLabel(key.lower())
            name.setObjectName("caption")
            self._grid.addWidget(name, row, 0)

            bar = _MiniBar(value)
            self._grid.addWidget(bar, row, 1)

            text = QLabel("n/a" if value is None else f"{value:.2f}")
            text.setObjectName("mono")
            text.setStyleSheet(f"color: {theme.score_colour(value)};")
            text.setAlignment(Qt.AlignmentFlag.AlignRight
                              | Qt.AlignmentFlag.AlignVCenter)
            text.setMinimumWidth(38)
            self._grid.addWidget(text, row, 2)

            share = pipe.num((coverage or {}).get(key))
            if share is not None:
                tip = (f"{key}: {'' if value is None else f'{value:.2f}, '}"
                       f"built from {share * 100:.0f}% of its inputs")
                for widget in (name, bar, text):
                    widget.setToolTip(tip)
            self._rows.append((name, bar, text))

        self._grid.setColumnStretch(1, 1)


class _MiniBar(QWidget):
    """A 0-10 bar, painted."""

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self._value = value
        self.setFixedHeight(7)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event):
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(theme.BG))
        value = pipe.num(self._value)
        if value is None:
            return
        width = int(round(rect.width() * max(0.0, min(10.0, value)) / 10.0))
        if width:
            painter.fillRect(rect.left(), rect.top(), width, rect.height(),
                             QColor(theme.score_colour(value)))


class TickerDetail(QWidget):
    """The ticker-detail body: header, two columns, notes."""

    add_to_selection = pyqtSignal(str)
    watch_requested = pyqtSignal(str)
    reevaluate_requested = pyqtSignal(str)
    note_changed = pyqtSignal(str, str, str)        # ticker, text, revisit
    lookup_requested = pyqtSignal(str)              # a symbol to resolve/score

    def __init__(self, notes=None, parent=None):
        super().__init__(parent)
        self._notes = notes
        self._subject = None
        self._scan = None
        self._sizing = {}
        # Records for tickers that are not in the scan, scored on demand by
        # the lookup box. Keyed by ticker; the subject may be one of these.
        self._external = {}

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(NOTES_SAVE_MS)
        self._save_timer.timeout.connect(self._save_note)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # ── lookup: any symbol becomes the subject ──────────────────
        # At the top of the panel, because it is the way in that does not
        # depend on the ticker already being in the scan.
        lookup = QHBoxLayout()
        lookup.setSpacing(6)

        self.lookup_box = QLineEdit()
        self.lookup_box.setPlaceholderText("look up any symbol")
        self.lookup_box.setMaximumWidth(170)
        self.lookup_box.setToolTip(
            "Any symbol. A name already in the scan shows immediately; "
            "anything else is scored on demand through the same single-ticker "
            "path the evaluator's batch mode uses.\n\n"
            "A bare Canadian symbol is tried against .TO, .V, .CN and .NE, "
            "and you are asked which listing you meant.")
        self.lookup_box.returnPressed.connect(self._on_lookup)
        lookup.addWidget(self.lookup_box)

        self.lookup_button = Chip("look up")
        self.lookup_button.clicked.connect(self._on_lookup)
        lookup.addWidget(self.lookup_button)

        self.lookup_state = QLabel("")
        self.lookup_state.setObjectName("faint")
        self.lookup_state.setWordWrap(True)
        lookup.addWidget(self.lookup_state, 1)

        outer.addLayout(lookup)

        # Resolved listings to choose between, and recent subjects to reopen.
        self._choice_row = QHBoxLayout()
        self._choice_row.setSpacing(4)
        outer.addLayout(self._choice_row)

        self._recent_row = QHBoxLayout()
        self._recent_row.setSpacing(4)
        outer.addLayout(self._recent_row)

        # ── header: subject, source, buttons ────────────────────────
        head = QHBoxLayout()
        head.setSpacing(8)

        self.symbol = QLabel("—")
        self.symbol.setObjectName("panelTitle")
        head.addWidget(self.symbol)

        self.composite = QLabel("")
        self.composite.setObjectName("mono")
        head.addWidget(self.composite)

        self.rating = QLabel("")
        self.rating.setObjectName("caption")
        head.addWidget(self.rating)

        head.addStretch(1)

        self.add_chip = Chip("add to selection")
        self.add_chip.clicked.connect(
            lambda: self._subject and self.add_to_selection.emit(self._subject))
        head.addWidget(self.add_chip)

        self.watch_chip = Chip("+ watch")
        self.watch_chip.setToolTip("add to the watchlist (Phase 3)")
        self.watch_chip.setEnabled(False)
        self.watch_chip.clicked.connect(
            lambda: self._subject and self.watch_requested.emit(self._subject))
        head.addWidget(self.watch_chip)

        self.reeval_chip = Chip("re-evaluate")
        self.reeval_chip.setToolTip("score this ticker again, ignoring the cache")
        self.reeval_chip.clicked.connect(
            lambda: self._subject and self.reevaluate_requested.emit(self._subject))
        head.addWidget(self.reeval_chip)

        outer.addLayout(head)

        self.source = QLabel("")
        self.source.setObjectName("faint")
        outer.addWidget(self.source)

        # ── body ────────────────────────────────────────────────────
        self.message = Message("select a candidate to see its detail")
        outer.addWidget(self.message)

        self.columns = QWidget()
        columns = QHBoxLayout(self.columns)
        columns.setContentsMargins(0, 2, 0, 0)
        columns.setSpacing(16)

        columns.addWidget(self._build_left(), 1)
        columns.addWidget(self._vline())
        columns.addWidget(self._build_right(), 1)

        outer.addWidget(self.columns, 1)
        self.columns.hide()

    # ── construction ────────────────────────────────────────────────
    @staticmethod
    def _vline():
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setStyleSheet(f"color: {theme.PANEL_BORDER};")
        return line

    def _build_left(self):
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(5)

        self.name_label = QLabel("")
        self.name_label.setWordWrap(True)
        column.addWidget(self.name_label)

        self.where_label = QLabel("")
        self.where_label.setObjectName("caption")
        column.addWidget(self.where_label)

        column.addSpacing(4)
        self.dims = DimensionBars()
        column.addWidget(self.dims)

        column.addSpacing(4)
        self.flags_label = QLabel("")
        self.flags_label.setObjectName("caption")
        self.flags_label.setWordWrap(True)
        column.addWidget(self.flags_label)

        column.addSpacing(4)
        guide_head = QLabel("sized guidance")
        guide_head.setObjectName("caption")
        column.addWidget(guide_head)

        self.guide_label = QLabel("")
        self.guide_label.setWordWrap(True)
        self.guide_label.setObjectName("mono")
        column.addWidget(self.guide_label)

        self.corr_label = QLabel("")
        self.corr_label.setObjectName("caption")
        self.corr_label.setWordWrap(True)
        column.addWidget(self.corr_label)

        column.addStretch(1)
        return holder

    def _build_right(self):
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(5)

        self.frameworks = QGridLayout()
        self.frameworks.setHorizontalSpacing(8)
        self.frameworks.setVerticalSpacing(3)
        column.addLayout(self.frameworks)

        column.addSpacing(6)
        spark_head = QLabel(f"price, {SPARK_WEEKS} weeks")
        spark_head.setObjectName("caption")
        column.addWidget(spark_head)

        self.spark = Sparkline()
        column.addWidget(self.spark)

        self.spark_note = QLabel("")
        self.spark_note.setObjectName("faint")
        column.addWidget(self.spark_note)

        column.addSpacing(6)
        notes_head = QHBoxLayout()
        label = QLabel("notes")
        label.setObjectName("caption")
        notes_head.addWidget(label)
        notes_head.addStretch(1)
        self.notes_saved = QLabel("")
        self.notes_saved.setObjectName("faint")
        notes_head.addWidget(self.notes_saved)
        column.addLayout(notes_head)

        self.notes_box = QPlainTextEdit()
        self.notes_box.setPlaceholderText("why this name is interesting, "
                                          "and what would change your mind")
        self.notes_box.setMinimumHeight(56)
        self.notes_box.textChanged.connect(self._on_note_edited)
        column.addWidget(self.notes_box, 1)

        revisit_row = QHBoxLayout()
        revisit_label = QLabel("revisit")
        revisit_label.setObjectName("caption")
        revisit_row.addWidget(revisit_label)
        self.revisit_box = QLineEdit()
        self.revisit_box.setPlaceholderText("YYYY-MM-DD")
        self.revisit_box.setMaximumWidth(110)
        self.revisit_box.textChanged.connect(self._on_note_edited)
        revisit_row.addWidget(self.revisit_box)
        revisit_row.addStretch(1)
        column.addLayout(revisit_row)

        return holder

    # ── lookup ──────────────────────────────────────────────────────
    def _on_lookup(self):
        """Enter, or the look-up button."""
        symbol = self.lookup_box.text().strip().upper()
        if not symbol:
            return
        self._clear_choices()
        self.lookup_requested.emit(symbol)

    def set_lookup_state(self, text, level="faint"):
        """The one-line state beside the box: working, or why not."""
        self.lookup_state.setText(str(text or ""))
        colour = {"faint": theme.FAINT, "warn": theme.AMBER,
                  "busy": theme.CYAN}.get(level, theme.FAINT)
        self.lookup_state.setStyleSheet(f"color: {colour};")

    def _clear_choices(self):
        while self._choice_row.count():
            item = self._choice_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    def show_listing_choice(self, options):
        """Ask which listing a bare symbol meant.

        A bare "SHOP" is the US line or Toronto, and the two are different
        companies’ worth of different. Rather than picking one, every
        listing that returned price history is offered.
        """
        self._clear_choices()
        if not options:
            return

        label = QLabel("which listing?")
        label.setObjectName("caption")
        self._choice_row.addWidget(label)

        for option in options:
            ticker = option.get("ticker")
            chip = Chip(f"{ticker} \u00b7 {venue(ticker)}")
            bits = [t for t in (option.get("name"),
                                option.get("currency")) if t]
            if option.get("last_close") is not None:
                bits.append(f"last {option['last_close']:,.2f}")
            chip.setToolTip(" \u00b7 ".join(str(b) for b in bits))
            chip.clicked.connect(
                lambda _c, t=ticker: self.lookup_requested.emit(t))
            self._choice_row.addWidget(chip)

        self._choice_row.addStretch(1)

    def set_recent(self, tickers):
        """The recent-lookups chip row, kept in settings.json."""
        while self._recent_row.count():
            item = self._recent_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        if not tickers:
            return

        label = QLabel("recent")
        label.setObjectName("faint")
        self._recent_row.addWidget(label)

        for ticker in tickers:
            chip = Chip(ticker)
            chip.setToolTip(f"show {ticker} again")
            chip.clicked.connect(
                lambda _c, t=ticker: self.lookup_requested.emit(t))
            self._recent_row.addWidget(chip)
        self._recent_row.addStretch(1)

    def remember_external(self, ticker, record):
        """Hold a record for a ticker the scan does not have."""
        symbol = str(ticker or "").strip().upper()
        if symbol and isinstance(record, dict):
            self._external[symbol] = record

    # ── data in ─────────────────────────────────────────────────────
    def set_scan(self, scan):
        self._scan = scan
        if self._subject:
            self.show_ticker(self._subject, source=self._last_source)

    def set_sizing(self, sizing_by_ticker):
        self._sizing = dict(sizing_by_ticker or {})
        if self._subject:
            self._render_sizing()

    _last_source = "selected in candidates"

    def subject(self):
        return self._subject

    def show_ticker(self, ticker, source="selected in candidates"):
        """Make `ticker` the subject and draw it."""
        symbol = str(ticker or "").strip().upper()
        self._last_source = source
        if not symbol or self._scan is None:
            return

        candidate = self._scan.by_ticker.get(symbol)
        record = self._scan.scored_by_ticker.get(symbol)
        external = self._external.get(symbol)

        if candidate is None and record is None and external is None:
            self._subject = symbol
            self.symbol.setText(symbol)
            self.composite.setText("")
            self.rating.setText("")
            self.source.setText("not in the current scan")
            self.message.setText(
                f"{symbol} is not in this scan's output. Use the lookup box "
                f"above to score it on demand.")
            self.message.show()
            self.columns.hide()
            return

        if record is None and external is not None:
            record = external

        self._subject = symbol
        self.message.hide()
        self.columns.show()

        merged = dict(record or {})
        merged.update({k: v for k, v in (candidate or {}).items()
                       if v is not None})

        self._render_header(symbol, merged, source)
        self._render_left(symbol, merged)
        self._render_right(symbol, merged)
        self._render_sizing()
        self._load_note(symbol)

    # ── rendering ───────────────────────────────────────────────────
    def _render_header(self, symbol, record, source):
        self.symbol.setText(symbol)

        composite = pipe.num(record.get("composite"))
        self.composite.setText("n/a" if composite is None else f"{composite:.2f}")
        self.composite.setStyleSheet(f"color: {theme.score_colour(composite)};")
        self.composite.setToolTip(
            f"composite — {theme.score_band(composite)}")

        rating = record.get("rating") or ""
        self.rating.setText(str(rating).lower())

        scored_age = age_seconds(record.get("price_as_of")) or \
            (self._scan.age if self._scan else None)
        self.source.setText(f"{source} · scored {describe_age(scored_age)}")

    def _render_left(self, symbol, record):
        name = record.get("name") or "name unknown"
        self.name_label.setText(str(name))

        sector = record.get("sector") or "sector unknown"
        currency = record.get("quote_currency") or "?"
        industry = record.get("industry")
        where = f"{sector} · {currency} · {venue(symbol)}"
        if industry and industry != sector:
            where = f"{sector} — {industry} · {currency} · {venue(symbol)}"
        self.where_label.setText(where)

        self.dims.set_dims(record.get("dims"), record.get("dim_coverage"))

        flags = (record.get("position_guidance") or {}).get("risk_flags") or []
        if not flags:
            flags = (record.get("sizing") or {}).get("risk_flags") or []
        warnings = record.get("warnings") or []
        parts = []
        if flags:
            parts.append("flags: " + ", ".join(str(f) for f in flags))
        if warnings:
            parts.append(str(warnings[0]))
        self.flags_label.setText("\n".join(parts) or "no risk flags")
        self.flags_label.setStyleSheet(
            f"color: {theme.AMBER if flags else theme.MUTED};")

    def _render_right(self, symbol, record):
        while self.frameworks.count():
            item = self.frameworks.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        frameworks = record.get("frameworks") or {}
        piotroski = frameworks.get("piotroski") or {}
        altman = frameworks.get("altman_z") or {}

        rows = []

        if piotroski:
            score = pipe.num(piotroski.get("score"))
            maximum = piotroski.get("max") or 9
            colour = (theme.GREEN if score is not None and score >= 7
                      else theme.AMBER if score is not None and score >= 4
                      else theme.RED)
            rows.append(("piotroski",
                         f"{'n/a' if score is None else int(score)}/{maximum}"
                         f"  {piotroski.get('label') or ''}".strip(),
                         colour, "nine accounting signals; higher is better"))

        if altman:
            score = pipe.num(altman.get("score"))
            zone = str(altman.get("zone") or "")
            colour = (theme.GREEN if "safe" in zone.lower()
                      else theme.RED if "distress" in zone.lower()
                      else theme.AMBER)
            rows.append(("altman z",
                         f"{'n/a' if score is None else f'{score:.2f}'}  {zone}".strip(),
                         colour, altman.get("warning") or
                         "bankruptcy-risk score; higher is safer"))

        trend = record.get("roa_trend")
        trend_colour = {"improving": theme.GREEN, "flat": theme.GREEN,
                        "mixed": theme.AMBER,
                        "deteriorating": theme.RED}.get(trend, theme.FAINT)
        years = record.get("trend_years_available")
        rows.append(("roa trend", f"{trend or 'unknown'}"
                     + (f"  ({years}y)" if years else ""),
                     trend_colour,
                     "direction of the multi-year return-on-assets series"))

        positive = record.get("fcf_positive_years")
        available = ((record.get("trend_detail") or {}).get("fcf_years_available"))
        if positive is not None:
            text = f"{positive}" + (f" of {available}" if available else "")
            colour = theme.GREEN
            if available and positive * 2 <= available:
                colour = theme.RED
            elif available and positive < available:
                colour = theme.AMBER
            rows.append(("fcf positive", text, colour,
                         "years with positive free cash flow in the window"))

        pattern = record.get("divergence_pattern") or "neutral"
        pattern_colour = {"price_disconnect": theme.GREEN,
                          "trend_confirms_decline": theme.RED}.get(pattern,
                                                                   theme.FAINT)
        detail = (record.get("divergence_detail") or {}).get("reason") or \
            "is a depressed price contradicted or confirmed by the trend data"
        rows.append(("divergence", pattern.replace("_", " "), pattern_colour,
                     detail))

        for row, (label, value, colour, tip) in enumerate(rows):
            name = QLabel(label)
            name.setObjectName("caption")
            name.setToolTip(tip)
            self.frameworks.addWidget(name, row, 0)

            text = QLabel(value)
            text.setObjectName("mono")
            text.setStyleSheet(f"color: {colour};")
            text.setToolTip(tip)
            self.frameworks.addWidget(text, row, 1)

        self.frameworks.setColumnStretch(1, 1)

    def _render_sizing(self):
        if not self._subject or self._scan is None:
            return
        candidate = self._scan.by_ticker.get(self._subject)
        if candidate is None:
            self.guide_label.setText("not in the current scan")
            self.guide_label.setStyleSheet(f"color: {theme.FAINT};")
            self.corr_label.setText("")
            return

        live = self._sizing.get(self._subject) or candidate.get("sizing") or {}
        base = sizing_module.guide_short(live.get("base_guide"))
        adjusted = sizing_module.guide_short(live.get("adjusted_guide"))

        if not base:
            self.guide_label.setText("no guidance for this name")
            self.guide_label.setStyleSheet(f"color: {theme.FAINT};")
        elif adjusted and adjusted != base:
            self.guide_label.setText(f"{base}\n→ {adjusted}")
            self.guide_label.setStyleSheet(f"color: {theme.AMBER};")
        else:
            self.guide_label.setText(base)
            self.guide_label.setStyleSheet(f"color: {theme.CREAM};")

        lines = []
        cut = sizing_module.cut_label(live)
        partner = live.get("correlated_with")
        correlation = pipe.num(live.get("max_correlation"))
        if cut and partner and correlation is not None:
            lines.append(f"{cut} — max ρ {correlation:+.2f} "
                         f"({live.get('correlation_basis')}) with {partner}")
        elif live.get("note"):
            lines.append(str(live["note"]))

        conviction = live.get("conviction")
        scale = pipe.num(live.get("conviction_scale"))
        if conviction and scale is not None and scale != 1.0:
            lines.append(f"conviction {conviction.replace('_', ' ')}: "
                         f"size ×{scale:.2f}")

        self.corr_label.setText("\n".join(lines))

    # ── sparkline ───────────────────────────────────────────────────
    def set_sparkline(self, rows, note=""):
        closes = [pipe.num(r.get("close")) for r in (rows or [])]
        self.spark.set_closes([c for c in closes if c is not None])
        if rows:
            first, last = closes[0], closes[-1]
            if first and last:
                change = 100.0 * (last - first) / first
                note = note or (f"{change:+.1f}% over the window "
                                f"· last {last:,.2f}")
        self.spark_note.setText(note or "")

    # ── notes ───────────────────────────────────────────────────────
    def _load_note(self, ticker):
        if self._notes is None:
            return
        entry = self._notes.get(ticker)
        for widget in (self.notes_box, self.revisit_box):
            widget.blockSignals(True)
        self.notes_box.setPlainText(entry.get("text") or "")
        self.revisit_box.setText(entry.get("revisit") or "")
        for widget in (self.notes_box, self.revisit_box):
            widget.blockSignals(False)
        self._show_saved(entry.get("saved_at"))

    def _on_note_edited(self):
        self._save_timer.start()
        self.notes_saved.setText("unsaved")

    def _save_note(self):
        if not self._subject or self._notes is None:
            return
        text = self.notes_box.toPlainText()
        revisit = self.revisit_box.text().strip() or None
        if self._notes.set(self._subject, text, revisit):
            self._show_saved(dt.datetime.now().isoformat(timespec="seconds"))
            self.note_changed.emit(self._subject, text, revisit or "")
        else:
            self.notes_saved.setText("save failed")

    def _show_saved(self, stamp):
        if not stamp:
            self.notes_saved.setText("")
            return
        self.notes_saved.setText(f"saved {describe_age(age_seconds(stamp))}")

    def flush_notes(self):
        """Save a pending note now. Called when the window closes."""
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_note()
