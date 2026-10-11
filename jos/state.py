r"""jos/state.py - the files the app owns, and nothing the pipeline owns.

Section 8: settings, notes, dated history snapshots, status, caches, logs. All
under <project>/data, all written atomically, none of it ever written into the
pipeline's directory.

Atomic writes reuse stocks_common.write_json rather than reimplementing it,
and that is not only about duplication. The pipeline's version retries
os.replace on PermissionError, because on Windows a real-time virus scanner
or OneDrive opens a file the moment it is created and holds it for a few
milliseconds - long enough for the replace to fail. A hand-rolled tmp+replace
here would hit that and lose a note. The fallback below (for when the pipeline
is not importable) carries the same retry for the same reason.

Reads are defensive everywhere: a missing file, a half-written one and one a
sync tool has mangled all mean "no data", never an exception. Section 9b
requires that explicitly, because a synced data folder delivers exactly those.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import time
from pathlib import Path

from . import pipeline as pipe
from .paths import (APP_DATA_DIR, HISTORY_DIR, HOLDINGS_SCORED_PATH,
                    NOTES_PATH, SETTINGS_PATH, STATUS_PATH, ensure_app_dirs)

# ─── DEFAULT SETTINGS ────────────────────────────────────────────────────
# The keys section 9 and section 10 name, with the brief's own defaults, plus
# the few the app needs for itself. Unknown keys in a settings file are kept
# on write, so a newer build's settings survive an older one reading them.
DEFAULTS = {
    # auto-update (section 9)
    "auto_refresh_on_logon": True,
    "auto_refresh_on_wake": True,
    "open_window_on_logon": True,
    "open_window_on_wake": False,
    "min_minutes_between_scans": 60,
    "network_wait_minutes": 5,
    "stale_warning_days": 3,

    # Section 9b: a machine with this off never runs the pipeline. The Mac is
    # meant to be the view-only one on a synced data folder.
    "scan_enabled": True,

    # Arguments handed to scan_report.py. --include-canada is here because
    # that is how this account's scan is normally run; it is a setting rather
    # than a literal so it can change without touching code. Every flag in
    # the default list is one scan_report's own CLI defines.
    "scan_args": ["--include-canada"],

    # holdings health (section 10). health_green_from defaults to None, which
    # means "use the evaluator's COLOUR_GOOD" - the brief's 7.5 predates the
    # v5.5 recalibration that moved it to 6.25, and hardcoding the stale
    # number here would make the app disagree with the terminal report.
    "health_review_drop": 1.5,
    "health_watch_drop": 0.7,
    "health_lookback_days": 30,
    "health_min_history_days": 14,
    "health_red_below": 5.0,
    "health_green_from": None,

    # Phase 3, inert until then. Empty means "skip the FRED source".
    "fred_api_key": "",

    # paths: both None means "work it out", which is the normal case
    "pipeline_dir": None,
    "data_dir": None,

    # UI state the app remembers across launches
    "window_geometry": None,
    "window_maximised": False,
    "views": {"ratings": "candidates", "correlation": "heatmap",
              "calendar": "earnings"},
    "recent_lookups": [],
    "sizing": {},
    "history_keep_days": 90,
}


def _write_json_atomic(document, path) -> Path:
    """Atomic JSON write. The pipeline's implementation when it is available.

    The fallback is the same shape, including the PermissionError retry: the
    whole reason the pipeline grew one is that a Windows file-scanner holds a
    newly created file briefly, and an app that writes notes on every
    keystroke would notice.
    """
    path = Path(path)
    if pipe.ready() and pipe.common is not None:
        return pipe.common.write_json(document, path,
                                      default=pipe.common.json_default)

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False, default=str)
        for attempt in range(4):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.1 * (2 ** attempt))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return path


def read_json(path, default=None):
    """Parsed JSON, or `default`. Never raises.

    Covers the file not existing yet, a sync tool mid-write, and a conflicted
    copy that is not JSON at all. The caller keeps whatever it had.
    """
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except Exception:                               # noqa: BLE001
        return default


def write_json(document, path) -> bool:
    """Write atomically. False on failure rather than an exception.

    Nothing the app stores is worth taking the window down for.
    """
    try:
        _write_json_atomic(document, path)
        return True
    except Exception:                               # noqa: BLE001
        return False


# ─── SETTINGS ────────────────────────────────────────────────────────────

class Settings:
    """settings.json, with defaults filled in and unknown keys preserved.

    Per machine, never in a synced folder: it holds window geometry and view
    choices, which are properties of the screen in front of you, not of the
    portfolio. Section 9b says so explicitly.
    """

    def __init__(self, path=None):
        self.path = Path(path) if path else SETTINGS_PATH
        self._data = dict(DEFAULTS)
        self._loaded_raw = {}
        self.load()

    def load(self):
        stored = read_json(self.path, default=None)
        if isinstance(stored, dict):
            self._loaded_raw = stored
            merged = dict(DEFAULTS)
            merged.update(stored)
            # One level of merge for the nested dicts, so a file written by a
            # build that knew fewer view slots does not drop the new ones.
            for key in ("views", "sizing"):
                if isinstance(stored.get(key), dict):
                    nested = dict(DEFAULTS[key])
                    nested.update(stored[key])
                    merged[key] = nested
            self._data = merged
        return self._data

    def save(self) -> bool:
        # Keys this build does not know about are written back untouched.
        document = dict(self._loaded_raw)
        document.update(self._data)
        return write_json(document, self.path)

    def get(self, key, default=None):
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        self._data[key] = value

    def update(self, **values):
        self._data.update(values)

    def view(self, panel, default=None):
        """The remembered view for a dropdown panel."""
        views = self._data.get("views") or {}
        return views.get(panel, default or (DEFAULTS["views"].get(panel)))

    def set_view(self, panel, name):
        views = dict(self._data.get("views") or {})
        views[panel] = name
        self._data["views"] = views

    def remember_lookup(self, ticker, limit=8):
        """Push a ticker onto the recent-lookups chip row, newest first."""
        symbol = str(ticker or "").strip().upper()
        if not symbol:
            return
        recent = [t for t in (self._data.get("recent_lookups") or [])
                  if t != symbol]
        recent.insert(0, symbol)
        self._data["recent_lookups"] = recent[:limit]

    def green_from(self):
        """The green threshold for holdings health.

        None in settings means "whatever the evaluator currently calls good",
        which is the only value that keeps the app and the report agreeing.
        """
        configured = self.get("health_green_from")
        if isinstance(configured, (int, float)):
            return float(configured)
        return pipe.colour_thresholds()[0]

    def scan_args(self):
        """The scan_report.py arguments, as a list of strings."""
        value = self.get("scan_args")
        if isinstance(value, str):
            return value.split()
        if isinstance(value, (list, tuple)):
            return [str(a) for a in value]
        return []

    def __repr__(self):
        return f"Settings({self.path})"


# ─── NOTES ───────────────────────────────────────────────────────────────

class Notes:
    """notes.json: {ticker: {text, revisit, saved_at}}.

    Keyed by ticker so a note follows the name whether it arrived through the
    candidates table or a lookup. Saved on a debounce by the caller; this
    class only owns the file.
    """

    def __init__(self, path=None):
        self.path = Path(path) if path else NOTES_PATH
        self._data = {}
        self.load()

    def load(self):
        stored = read_json(self.path, default=None)
        self._data = stored if isinstance(stored, dict) else {}
        return self._data

    def get(self, ticker) -> dict:
        entry = self._data.get(str(ticker or "").strip().upper())
        return dict(entry) if isinstance(entry, dict) else {}

    def set(self, ticker, text, revisit=None) -> bool:
        symbol = str(ticker or "").strip().upper()
        if not symbol:
            return False
        body = str(text or "")
        if not body.strip() and not revisit:
            self._data.pop(symbol, None)
        else:
            self._data[symbol] = {
                "text": body,
                "revisit": revisit or None,
                "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
        return write_json(self._data, self.path)

    def tickers(self):
        return sorted(self._data)


# ─── STATUS (shared with the autorun) ────────────────────────────────────
# The autorun writes this at every transition; the app reads it for the
# toolbar chip. Separate from the pipeline's own run_status.json, which says
# what the SCAN did - this one says what the REFRESH did, including the parts
# that happen before a scan starts (waiting for the network) and the case
# where no scan was needed at all.

STATES = ("idle", "waiting_for_network", "refreshing", "failed")


def read_status(path=None) -> dict:
    """The autorun's status document, or an idle default."""
    stored = read_json(path or STATUS_PATH, default=None)
    if not isinstance(stored, dict):
        return {"state": "idle", "started_at": None, "finished_at": None,
                "stage": None, "last_error": None, "reason": None}
    return stored


def write_status(state, stage=None, last_error=None, reason=None,
                 started_at=None, path=None) -> bool:
    """Record a transition. Unknown states are stored rather than rejected,
    so a newer autorun writing a new state does not blank the chip."""
    document = read_status(path)
    now = dt.datetime.now().isoformat(timespec="seconds")
    document["state"] = state
    document["stage"] = stage
    document["last_error"] = last_error
    if reason is not None:
        document["reason"] = reason
    if state == "refreshing" and not document.get("started_at"):
        document["started_at"] = started_at or now
    if started_at is not None:
        document["started_at"] = started_at
    if state in ("idle", "failed"):
        document["finished_at"] = now
    document["updated_at"] = now
    document["pid"] = os.getpid()
    return write_json(document, path or STATUS_PATH)


# ─── HISTORY SNAPSHOTS ───────────────────────────────────────────────────
# Section 8: dated copies of sized_candidates.json, taken only after a scan
# that actually produced new data, pruned past ~90 days. This powers Phase 2's
# "changes since last scan" and the 30-day score comparison.
#
# Worth knowing for Phase 2: the pipeline ALREADY archives every completed run
# to <data>/archive/<timestamp>/ and keeps 30 of them (scan_report's
# --archive-keep). The app's own history is still the right thing to build on,
# because it is bounded by days rather than runs and lives where the app can
# prune it, but the archive is a second source if 30 runs reaches further back.

SNAPSHOT_FORMAT = "%Y-%m-%d_%H%M"


def snapshot_name(generated_at=None) -> str:
    """The YYYY-MM-DD_HHMM name for a snapshot of this scan.

    Named after the scan's own generated_at rather than the wall clock, so
    re-running the app does not file the same scan under a second name.
    """
    moment = None
    if generated_at:
        try:
            moment = dt.datetime.fromisoformat(str(generated_at)[:19])
        except ValueError:
            moment = None
    return (moment or dt.datetime.now()).strftime(SNAPSHOT_FORMAT)


def latest_snapshot(directory=None):
    """(path, parsed document) of the newest snapshot, or (None, None)."""
    root = Path(directory or HISTORY_DIR)
    if not root.is_dir():
        return None, None
    files = sorted((p for p in root.glob("*.json") if p.is_file()), reverse=True)
    for path in files:
        document = read_json(path)
        if isinstance(document, dict):
            return path, document
    return None, None


def previous_snapshot(generated_at, directory=None):
    """The newest snapshot that is NOT the scan identified by `generated_at`.

    "Changes since last scan" means the scan before this one, and the app
    files a snapshot of every scan it loads - so by the time the view is
    drawn a second time, the newest snapshot IS the scan on screen and
    comparing against it shows nothing changed. This skips it.

    Matching on generated_at rather than on the file name because a snapshot
    is named after the scan's own timestamp, and two scans a minute apart
    round to the same minute in the name.
    """
    root = Path(directory or HISTORY_DIR)
    if not root.is_dir():
        return None, None
    for path in sorted((p for p in root.glob("*.json") if p.is_file()),
                       reverse=True):
        document = read_json(path)
        if not isinstance(document, dict):
            continue
        if generated_at and document.get("generated_at") == generated_at:
            continue
        return path, document
    return None, None


def snapshot_taken(generated_at, directory=None) -> bool:
    """Whether this scan has already been filed."""
    root = Path(directory or HISTORY_DIR)
    return (root / f"{snapshot_name(generated_at)}.json").is_file()


def take_snapshot(sized_document, directory=None, keep_days=90) -> bool:
    """File a copy of a scan, if it is new. True when one was written.

    "New" is decided on generated_at, not on the file's mtime: the pipeline
    rewrites sized_candidates.json on every run, including the runs where
    every stage was fresh and nothing actually changed, and a snapshot per
    app launch would bury the real history.
    """
    if not isinstance(sized_document, dict):
        return False
    generated_at = sized_document.get("generated_at")

    root = Path(directory or HISTORY_DIR)
    root.mkdir(parents=True, exist_ok=True)

    _, previous = latest_snapshot(root)
    if isinstance(previous, dict) and previous.get("generated_at") == generated_at:
        return False
    if snapshot_taken(generated_at, root):
        return False

    written = write_json(sized_document, root / f"{snapshot_name(generated_at)}.json")
    if written:
        prune_snapshots(root, keep_days)
    return written


def prune_snapshots(directory=None, keep_days=90) -> int:
    """Delete snapshots older than keep_days. Returns how many went.

    The newest is never pruned whatever its age: it is the only thing a
    "changes since last scan" view has to compare against, and a machine that
    has been off for four months should get a stale comparison rather than
    none.
    """
    root = Path(directory or HISTORY_DIR)
    if not root.is_dir() or not keep_days:
        return 0

    files = sorted((p for p in root.glob("*.json") if p.is_file()), reverse=True)
    cutoff = dt.datetime.now() - dt.timedelta(days=int(keep_days))
    removed = 0
    for path in files[1:]:
        try:
            moment = dt.datetime.strptime(path.stem, SNAPSHOT_FORMAT)
        except ValueError:
            continue                                # not ours; leave it alone
        if moment < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def snapshot_nearest(days_ago, directory=None):
    """The snapshot closest to `days_ago` days back, or (None, None).

    Phase 2's holdings health compares against "the snapshot closest to 30
    days ago", which is not the same as the oldest or the newest.
    """
    root = Path(directory or HISTORY_DIR)
    if not root.is_dir():
        return None, None
    target = dt.datetime.now() - dt.timedelta(days=float(days_ago))
    best, best_gap = None, None
    for path in root.glob("*.json"):
        try:
            moment = dt.datetime.strptime(path.stem, SNAPSHOT_FORMAT)
        except ValueError:
            continue
        gap = abs((moment - target).total_seconds())
        if best_gap is None or gap < best_gap:
            best, best_gap = path, gap
    if best is None:
        return None, None
    return best, read_json(best)


def oldest_snapshot(directory=None):
    """(path, parsed document) of the earliest snapshot, or (None, None)."""
    root = Path(directory or HISTORY_DIR)
    if not root.is_dir():
        return None, None
    for path in sorted(root.glob("*.json")):
        document = read_json(path)
        if isinstance(document, dict):
            return path, document
    return None, None


def history_days(directory=None):
    """How many days of history exist, or None when there are none.

    Measured from the OLDEST snapshot to now, because that is what decides
    whether a monthly comparison is possible at all. Holdings health reads
    this to tell "the score is flat" apart from "there is nothing to compare
    against yet", which look identical in a table and mean opposite things.
    """
    root = Path(directory or HISTORY_DIR)
    if not root.is_dir():
        return None

    stamps = []
    for path in root.glob("*.json"):
        try:
            stamps.append(dt.datetime.strptime(path.stem, SNAPSHOT_FORMAT))
        except ValueError:
            continue
    if not stamps:
        return None
    return (dt.datetime.now() - min(stamps)).total_seconds() / 86400.0


# ─── RE-SCORED HOLDINGS ──────────────────────────────────────────────────
# Section 8 names holdings_scored.json as app-owned state. It is a cache of
# the evaluator's own single-ticker output for each holding, so the health
# view can draw a status without re-running the network on every launch.

def read_holdings_scored(path=None) -> dict:
    """{ticker: record} from the cache, or {}."""
    document = read_json(path or HOLDINGS_SCORED_PATH)
    if not isinstance(document, dict):
        return {}
    records = document.get("records")
    return records if isinstance(records, dict) else {}


def holdings_scored_age(path=None):
    """Seconds since the holdings cache was written, or None."""
    document = read_json(path or HOLDINGS_SCORED_PATH)
    if not isinstance(document, dict):
        return None
    stamp = document.get("generated_at")
    if not stamp:
        return None
    try:
        written = dt.datetime.fromisoformat(str(stamp)[:19])
    except ValueError:
        return None
    return max(0.0, (dt.datetime.now() - written).total_seconds())


def write_holdings_scored(records, path=None) -> bool:
    """Cache the re-scored holdings."""
    return write_json({
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "count": len(records or {}),
        "records": records or {},
    }, path or HOLDINGS_SCORED_PATH)


def init() -> None:
    """Make the app's directories. Called once at startup."""
    ensure_app_dirs()
