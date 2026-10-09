r"""jos/paths.py - every path the app reads or writes, named once.

Two directories, and the split between them is the whole point:

  PIPELINE DATA (read-only)   the scan's own output. $STOCKS_DATA_DIR, else
                              <pipeline>/data. The app never writes here -
                              holdings.json, sized_candidates.json and the
                              rest belong to the pipeline, and a desktop view
                              that edits its source of truth is a second
                              writer nobody asked for.

  APP STATE (read-write)      <project>/data. Settings, notes, history
                              snapshots, caches, logs, the autorun's status
                              file. All of it reproducible except notes.json,
                              and none of it something the pipeline reads.

Resolution follows the pipeline's own rule rather than reimplementing it:
stocks_common.data_dir() honours $STOCKS_DATA_DIR over the base directory, so
pointing that variable at a synced folder moves the pipeline and the app
together. When the pipeline is not importable the same rule is applied here
directly, so the app can still find the data a previous scan left behind.

pathlib throughout and no literal separators, because section 9b of the brief
is a hard requirement: nothing Windows-specific may leak into the app code.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import pipeline as pipe

#: The project itself - the folder holding jostocks.py.
PROJECT_DIR = Path(__file__).resolve().parent.parent

#: App-owned state. Never the pipeline's.
APP_DATA_DIR = PROJECT_DIR / "data"

HISTORY_DIR = APP_DATA_DIR / "history"
LOG_DIR = APP_DATA_DIR / "logs"
FONT_DIR = PROJECT_DIR / "fonts"

# ── app-owned files (section 8) ──────────────────────────────────────────
SETTINGS_PATH = APP_DATA_DIR / "settings.json"
NOTES_PATH = APP_DATA_DIR / "notes.json"
STATUS_PATH = APP_DATA_DIR / "status.json"
CORR_CACHE_PATH = APP_DATA_DIR / "corr_matrix.json"
HOLDINGS_SCORED_PATH = APP_DATA_DIR / "holdings_scored.json"
WATCHLIST_PATH = APP_DATA_DIR / "watchlist.json"
MACRO_CALENDAR_PATH = APP_DATA_DIR / "macro_calendar.json"
EARNINGS_CACHE_PATH = APP_DATA_DIR / "earnings_cache.json"
REFRESH_LOCK_PATH = APP_DATA_DIR / "refresh.lock"


def pipeline_data_dir(override=None) -> Path:
    """Where the scan writes. $STOCKS_DATA_DIR wins, as it does in the pipeline.

    `override` is settings.json's `data_dir`, for an install that keeps the
    data somewhere neither the environment nor the pipeline folder implies.
    """
    if override:
        return Path(override).expanduser()

    env = os.environ.get("STOCKS_DATA_DIR")
    if env:
        return Path(env).expanduser()

    if pipe.ready() and pipe.common is not None:
        # The pipeline's own resolution, so the two cannot drift apart.
        return Path(pipe.common.data_dir(pipe.md.BASE_DIR))

    base = pipe.find_pipeline_dir() or PROJECT_DIR.parent
    return Path(base) / "data"


class DataPaths:
    """The pipeline files the app reads, resolved against one data directory.

    A class rather than module globals because the directory is not known at
    import time: it depends on settings.json, which is read after the pipeline
    bootstrap. Everything is a plain attribute, so a view can hold one of
    these and not care where it came from.
    """

    def __init__(self, data_dir):
        self.dir = Path(data_dir)

        # The four the brief names as inputs.
        self.sized = self.dir / "sized_candidates.json"
        self.clustered = self.dir / "clustered.json"
        self.scored = self.dir / "scored_candidates.json"
        self.holdings = self.dir / "holdings.json"

        # Written by the pipeline too, and worth reading: `sentiment` carries
        # the candidates table's sent column, `run_status` says whether the
        # last scan actually completed, and `archive` is the history the
        # pipeline already keeps.
        self.candidates = self.dir / "candidates.json"
        self.sentiment = self.dir / "sentiment.json"
        self.timing = self.dir / "timing_flags.json"
        self.exit_signals = self.dir / "exit_signals.json"
        self.run_status = self.dir / "run_status.json"
        self.archive = self.dir / "archive"

    def __repr__(self):
        return f"DataPaths({self.dir})"

    @property
    def watched(self):
        """Files whose change should reload a view."""
        return (self.sized, self.clustered, self.scored, self.holdings,
                self.sentiment, self.timing, self.exit_signals,
                self.run_status)

    def exists(self) -> bool:
        """Whether there is any scan output at all to show."""
        return self.sized.is_file()


def ensure_app_dirs() -> None:
    """Create the app's own directories. Never the pipeline's."""
    for directory in (APP_DATA_DIR, HISTORY_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
