r"""jos/datastore.py - read the pipeline's output, and notice when it changes.

Everything the views draw comes through a Scan object. It is a snapshot: load
it once, hand it round, and when the files change build a new one. That keeps
the views free of file handling and makes "reload without losing the
selection" a matter of swapping one object.

Three things worth stating about the loading:

  - Absent is normal. A stage may not have run yet, so every file reads back
    as None and the UI says "no data yet, scan running or not run" rather than
    erroring. Section 4 requires that.
  - Half-written is normal too. The pipeline writes atomically, but a synced
    data folder (section 9b) can hand over a conflicted copy. A read error
    keeps the last good Scan and waits for the next change event.
  - run_status.json is read as well as the four inputs the brief names,
    because without it the app cannot tell a scan that finished from one that
    halted at stage two and left four stale files behind - which is exactly
    when it most needs to stop presenting them as today's numbers.

The watcher watches the DIRECTORY, not the files. The pipeline writes tmp +
os.replace, and a replace drops the inode a per-file watch is attached to, so
on Windows the watch is silently lost after the first swap. A directory watch
survives it. Dionysus watches individual files for the opposite reason - its
data folder also holds files the widget itself writes, so a directory watch
fired several times per keystroke - but jostocks keeps its own state in a
separate directory, so that cost does not apply here.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from . import pipeline as pipe
from .paths import DataPaths
from .state import read_json

#: Debounce for a reload after a change event (section 2).
RELOAD_DEBOUNCE_MS = 300

# ─── Canadian venues ─────────────────────────────────────────────────────
# Yahoo's suffixes, with what they actually are. Taken from
# stocks_common.CAD_SUFFIXES and its comment: Toronto, TSX Venture, the CSE,
# and Cboe Canada - the last being where the CDRs trade, the CAD-hedged
# depositary receipts over US megacaps.
#
# These exist because "Canada" is not specific enough to put on screen. A list
# that covers only Toronto must say "TSX", not "Canada", and the only honest
# way to know which venues a list covers is to look at the suffixes in it.
VENUES = {
    ".TO": "TSX",
    ".V": "TSX Venture",
    ".CN": "CSE",
    ".NE": "Cboe Canada",
}

VENUE_SHORT = {".TO": "TSX", ".V": "TSXV", ".CN": "CSE", ".NE": "Cboe CA"}


def venue(ticker) -> str:
    """"TSX", "TSXV", "CSE", "Cboe CA" or "US" for one symbol."""
    symbol = str(ticker or "").strip().upper()
    for suffix, label in VENUE_SHORT.items():
        if symbol.endswith(suffix):
            return label
    return "US"


def is_canadian(ticker) -> bool:
    """Whether a symbol is any Canadian listing."""
    symbol = str(ticker or "").strip().upper()
    return any(symbol.endswith(suffix) for suffix in VENUES)


def venue_coverage(tickers) -> str:
    """Which venues a list of tickers actually covers, as a phrase.

    This is what keeps the app from calling a Toronto-only list "Canadian
    coverage". Derived from the data every time rather than from the scan's
    flags, because --include-canada says what was ASKED for and the suffixes
    say what arrived.
    """
    found = []
    for ticker in tickers or ():
        label = venue(ticker)
        if label not in found:
            found.append(label)
    if not found:
        return "no listings"

    canadian = [v for v in VENUE_SHORT.values() if v in found]
    if not canadian:
        return "US only"
    if "US" not in found:
        return (f"{canadian[0]} only" if len(canadian) == 1
                else " + ".join(canadian) + ", no US")
    return "US + " + " + ".join(canadian)


# ─── freshness ───────────────────────────────────────────────────────────

def parse_stamp(value):
    """An ISO timestamp from the pipeline, or None. Never raises."""
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value)[:19])
    except (TypeError, ValueError):
        return None


def age_seconds(value):
    """Seconds since an ISO timestamp, or None."""
    moment = parse_stamp(value)
    if moment is None:
        return None
    return max(0.0, (dt.datetime.now() - moment).total_seconds())


def describe_age(seconds) -> str:
    """"2h ago" / "3d ago" / "just now". Matches the report's register."""
    if seconds is None:
        return "unknown"
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 90:
        return f"{int(round(minutes))}m ago"
    hours = minutes / 60
    if hours < 36:
        return f"{int(round(hours))}h ago"
    return f"{int(round(hours / 24))}d ago"


def file_age_seconds(path):
    """Seconds since a file was last written, or None if it is not there."""
    try:
        return max(0.0, dt.datetime.now().timestamp() - Path(path).stat().st_mtime)
    except OSError:
        return None


# ─── the snapshot ────────────────────────────────────────────────────────

class Scan:
    """One read of the pipeline's output. Immutable once built.

    Nothing here computes sizing: that is jos.sizing's job, because it changes
    with the sliders and this does not.
    """

    def __init__(self, paths: DataPaths):
        self.paths = paths
        self.loaded_at = dt.datetime.now()

        self.sized = read_json(paths.sized)
        self.clustered = read_json(paths.clustered)
        self.scored = read_json(paths.scored)
        self.holdings_doc = read_json(paths.holdings)
        self.sentiment_doc = read_json(paths.sentiment)
        self.timing_doc = read_json(paths.timing)
        self.run_status = read_json(paths.run_status)

        # Fast lookups the views would otherwise rebuild on every paint.
        self.candidates = list((self.sized or {}).get("candidates") or [])
        self.by_ticker = {c.get("ticker"): c for c in self.candidates
                          if c.get("ticker")}
        self.params = dict((self.sized or {}).get("params") or {})
        self.sentiment = dict((self.sentiment_doc or {}).get("sentiment") or {})
        self.timing = dict((self.timing_doc or {}).get("flags") or {})

        self.scored_by_ticker = {
            record.get("ticker"): record
            for record in ((self.scored or {}).get("scored") or [])
            if record.get("ticker")
        }

        # position_sizer already re-scores every holding on each run and
        # writes the verdict here. The brief files holdings health under
        # Phase 2, but the pipeline has done the work, so Phase 1 shows the
        # real verdict instead of a blank column.
        self.holdings_review = list((self.sized or {}).get("holdings_review") or [])
        self.review_by_ticker = {r.get("ticker"): r for r in self.holdings_review
                                 if r.get("ticker")}

    # ── availability ─────────────────────────────────────────────────
    @property
    def ok(self) -> bool:
        """Whether there is a candidate list to draw."""
        return bool(self.candidates)

    @property
    def empty_reason(self) -> str:
        """Why there is nothing to show, in the person's terms."""
        if self.sized is None:
            if not self.paths.dir.is_dir():
                return (f"no data directory yet ({self.paths.dir}). "
                        f"Run the scan once, or set data_dir in settings.json.")
            return ("no data yet, scan running or not run - "
                    f"nothing has written {self.paths.sized.name}")
        if not self.candidates:
            note = (self.sized or {}).get("note")
            return note or "the last scan produced no candidates"
        return ""

    # ── holdings ─────────────────────────────────────────────────────
    @property
    def holdings(self) -> list:
        """Real holdings, example rows dropped.

        Preferred from sized_candidates.json, which position_sizer already
        normalised (upper-cased tickers, numeric coercion, _example dropped).
        holdings.json is the fallback for when no scan has run, and the
        _example filter is applied again here because that file is the
        hand-edited one.
        """
        from_scan = (self.sized or {}).get("holdings")
        if isinstance(from_scan, list) and from_scan:
            return [h for h in from_scan if isinstance(h, dict)
                    and not h.get("_example")]

        raw = (self.holdings_doc or {}).get("holdings")
        out = []
        for entry in (raw or []):
            if not isinstance(entry, dict) or entry.get("_example"):
                continue
            ticker = str(entry.get("ticker") or "").strip().upper()
            if not ticker or ticker == "...":
                continue
            out.append({
                "ticker": ticker,
                "shares": pipe.num(entry.get("shares")),
                "cost_basis": pipe.num(entry.get("cost_basis")),
                "currency": (str(entry["currency"]).strip().upper()
                             if isinstance(entry.get("currency"), str)
                             and entry["currency"].strip() else None),
                "entry_date": entry.get("entry_date"),
                "entry_price": pipe.num(entry.get("entry_price")),
            })
        return out

    @property
    def holding_tickers(self) -> list:
        return [h.get("ticker") for h in self.holdings if h.get("ticker")]

    def holding_currency(self, holding) -> str:
        """The currency a holding's cost basis is in.

        An explicit `currency` wins - holdings.json is hand-written and the
        person entering a cost basis knows what they paid in. Otherwise the
        scan's quote_currency for that name, then the suffix.
        """
        explicit = holding.get("currency")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip().upper()
        candidate = self.by_ticker.get(holding.get("ticker")) or {}
        quoted = candidate.get("quote_currency")
        if isinstance(quoted, str) and quoted.strip():
            return quoted.strip().upper()
        return "CAD" if is_canadian(holding.get("ticker")) else "USD"

    @property
    def mixed_currency(self) -> bool:
        """Whether holdings settle in more than one currency.

        Section 12 forbids summing across currencies without conversion, so
        the views ask this and label instead of adding.
        """
        return len({self.holding_currency(h) for h in self.holdings}) > 1

    # ── sizing defaults, from the scan that produced the file ────────
    def sizing_defaults(self) -> dict:
        """What the pipeline was run with. The sliders start here (section 6)."""
        params = self.params
        threshold = pipe.num(params.get("correlation_threshold"))
        factor = pipe.num(params.get("reduction_factor"))
        basis = params.get("correlation_basis")
        mode = params.get("reduction_mode")

        fallback_threshold, fallback_factor, fallback_basis = 0.70, 0.50, "raw"
        if pipe.ready() and pipe.ps is not None:
            fallback_threshold = getattr(pipe.ps, "CORR_THRESHOLD", 0.70)
            fallback_factor = getattr(pipe.ps, "REDUCTION_FACTOR", 0.50)
            fallback_basis = getattr(pipe.ps, "CORRELATION_BASIS", "raw")

        return {
            "threshold": fallback_threshold if threshold is None else threshold,
            "reduction_factor": fallback_factor if factor is None else factor,
            "basis": basis if basis in ("raw", "cleaned") else fallback_basis,
            "flat": mode == "flat",
        }

    # ── clusters ─────────────────────────────────────────────────────
    @property
    def clusters(self) -> list:
        return list((self.clustered or {}).get("clusters") or [])

    @property
    def standalone(self) -> list:
        return list((self.clustered or {}).get("standalone") or [])

    @property
    def standalone_notes(self) -> dict:
        return dict((self.clustered or {}).get("standalone_notes") or {})

    def cluster_order(self) -> list:
        """Tickers ordered so cluster members sit together.

        The heatmap needs this: members of one cluster adjacent, so a cluster
        shows up as a block on the diagonal, then standalone names, then
        holdings last (section 6). Holdings are appended by the caller, which
        knows which of them made it into the matrix.
        """
        order, seen = [], set()
        for cluster in self.clusters:
            for ticker in (cluster.get("members") or []):
                if ticker in self.by_ticker and ticker not in seen:
                    order.append(ticker)
                    seen.add(ticker)
        for ticker in self.standalone:
            if ticker in self.by_ticker and ticker not in seen:
                order.append(ticker)
                seen.add(ticker)
        # Anything the cluster file never mentioned, so nothing is dropped.
        for ticker in self.by_ticker:
            if ticker not in seen:
                order.append(ticker)
                seen.add(ticker)
        return order

    def cluster_for(self, ticker):
        """The cluster block a candidate belongs to, or None."""
        candidate = self.by_ticker.get(ticker) or {}
        index = (candidate.get("cluster") or {}).get("cluster_index")
        if index is None:
            return None
        clusters = self.clusters
        return clusters[index] if 0 <= index < len(clusters) else None

    # ── freshness and run state ──────────────────────────────────────
    @property
    def generated_at(self):
        return (self.sized or {}).get("generated_at")

    @property
    def age(self):
        return age_seconds(self.generated_at)

    def freshness_line(self) -> str:
        """"scored 2h ago, prices 2h ago, financials 3d ago" (section 6).

        Each figure comes from the stage that actually produced it, not from
        one file's mtime: the scored file carries financials_as_of and
        price_as_of per candidate, which is where "financials 3d ago" is
        really knowable.
        """
        parts = [f"scored {describe_age(self.age)}"]

        prices = [age_seconds(c.get("price_as_of")) for c in self.candidates]
        prices = [a for a in prices if a is not None]
        if prices:
            parts.append(f"prices {describe_age(min(prices))}")

        financials = [age_seconds(c.get("financials_as_of")) for c in self.candidates]
        financials = [a for a in financials if a is not None]
        if financials:
            parts.append(f"financials {describe_age(min(financials))}")

        return ", ".join(parts)

    @property
    def scan_completed(self):
        """Whether the last scan ran every required stage. None if unknown."""
        if not isinstance(self.run_status, dict):
            return None
        return bool(self.run_status.get("completed"))

    def stage_rows(self) -> list:
        """Per-stage status from run_status.json.

        The pipeline has seven stages, three of them optional - the brief's
        "stage 2 of 5" is out of date - so the count is read rather than
        assumed.
        """
        if not isinstance(self.run_status, dict):
            return []
        rows = self.run_status.get("stages")
        return list(rows) if isinstance(rows, list) else []

    def blocking_stages(self) -> list:
        if not isinstance(self.run_status, dict):
            return []
        rows = self.run_status.get("blocking_stages")
        return list(rows) if isinstance(rows, list) else []

    def coverage_line(self) -> str:
        """Which venues this scan actually covers."""
        return venue_coverage(list(self.by_ticker) + self.holding_tickers)

    # ── correlation panel metadata ───────────────────────────────────
    @property
    def correlation_panel(self) -> dict:
        return dict(self.params.get("correlation_panel") or {})

    def correlation_footer(self) -> str:
        """The heatmap's footer line, from params.correlation_panel.

        Section 6 asks for lookback years, trading days, T/N, MP threshold,
        signal modes, and what was excluded and why.
        """
        panel = self.correlation_panel
        if not panel:
            return "no correlation panel recorded for this scan"

        bits = []
        years = pipe.num(panel.get("lookback_years"))
        if years is not None:
            bits.append(f"{years:.2f}y lookback ({panel.get('period') or '?'})")
        if panel.get("assets_in_matrix"):
            bits.append(f"{panel['assets_in_matrix']} names")
        if panel.get("trading_days"):
            bits.append(f"{panel['trading_days']} trading days")
        ratio = pipe.num(panel.get("q_ratio"))
        if ratio is not None:
            bits.append(f"T/N {ratio:.2f}")
        threshold = pipe.num(panel.get("mp_threshold"))
        if threshold is not None:
            bits.append(f"MP edge {threshold:.3f}")
        if panel.get("signals_found") is not None:
            bits.append(f"{panel['signals_found']} signal modes")

        excluded = panel.get("excluded") or {}
        if excluded:
            bits.append(f"{len(excluded)} excluded")
        missing = panel.get("holdings_missing") or []
        if missing:
            bits.append(f"{len(missing)} holding(s) not in matrix")
        return " · ".join(bits)

    def correlation_warning(self):
        """params.correlation_panel's T/N warning, or None."""
        return self.correlation_panel.get("warning")

    def correlation_exclusions(self) -> dict:
        """{ticker: why it is not in the matrix}."""
        panel = self.correlation_panel
        out = dict(panel.get("excluded") or {})
        for ticker in (panel.get("holdings_missing") or []):
            out.setdefault(ticker, "no usable price history in the panel")
        return out

    def __repr__(self):
        return f"Scan({len(self.candidates)} candidates, {self.paths.dir})"


def load(paths: DataPaths) -> Scan:
    """Read everything. Never raises."""
    return Scan(paths)


# ─── the watcher ─────────────────────────────────────────────────────────

class DataWatcher(QObject):
    """Zero-polling watch over the pipeline's data directory.

    Emits `changed` at most once per burst. A scan touches eight files in
    sequence, each as tmp + replace, which arrives as a stream of directory
    events; without the debounce the app would rebuild every view eight times.
    """

    changed = pyqtSignal()

    def __init__(self, paths: DataPaths, debounce_ms=RELOAD_DEBOUNCE_MS,
                 parent=None):
        super().__init__(parent)
        self.paths = paths
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_event)
        self._watcher.fileChanged.connect(self._on_event)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._fire)

        self._stamps = self._current_stamps()
        self.arm()

    def arm(self):
        """Watch the data directory, and its parent until it exists.

        A directory that is not there yet cannot be watched, and the normal
        first-run case is that it is not: the pipeline creates it. Watching
        the parent until then means the app notices the first scan without a
        restart.
        """
        directory = self.paths.dir
        watched = set(self._watcher.directories())

        if directory.is_dir():
            if str(directory) not in watched:
                self._watcher.addPath(str(directory))
            parent = str(directory.parent)
            if parent in watched:
                self._watcher.removePath(parent)
        else:
            parent = directory.parent
            if parent.is_dir() and str(parent) not in watched:
                self._watcher.addPath(str(parent))

    def _current_stamps(self) -> dict:
        """(mtime, size) per watched file, to tell our changes from noise."""
        stamps = {}
        for path in self.paths.watched:
            try:
                info = path.stat()
                stamps[path] = (info.st_mtime_ns, info.st_size)
            except OSError:
                stamps[path] = None
        return stamps

    def _on_event(self, _path):
        self.arm()
        self._timer.start()

    def _fire(self):
        stamps = self._current_stamps()
        if stamps == self._stamps:
            return          # something else in the folder moved, not our files
        self._stamps = stamps
        self.changed.emit()

    def poke(self):
        """Force a reload on the next tick. Used by the refresh button."""
        self._stamps = {}
        self._timer.start()
