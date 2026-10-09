#!/usr/bin/env python3
r"""jostocks_autorun.py - refresh the data, and open the window at logon.

Run by the Startup-folder shortcut that install_autostart.ps1 creates, under
pythonw.exe so there is no console window. It does, in order:

  1. Take the refresh lock. If a refresh is already running, exit - two scans
     at once would double the traffic Yahoo sees from one client, and the
     pipeline's whole backoff policy assumes one.
  2. Wait for the network. A logon or a wake fires before Wi-Fi is back, so
     connectivity is probed with backoff for up to network_wait_minutes,
     writing status.json as waiting_for_network. If it never appears, the run
     finishes as failed with a reason and the existing data is left alone.
  3. Skip the scan if the data is fresher than min_minutes_between_scans.
  4. Otherwise run scan_report.py as a subprocess at below-normal priority
     with no console window - its own arguments and nothing invented. It
     already skips fresh stages, re-runs everything downstream of a stage
     that ran, and enforces market_data's caching and backoff; none of that
     is bypassed and no full refresh is forced.
  5. File a history snapshot.
  6. Write status.json at every transition.

Then, depending on --reason:

    logon   open the window, if open_window_on_logon is true
    wake    tell a running window to reload; do not pop one up unless
            open_window_on_wake is true
    manual  refresh only

WAKE, AND WHAT IT ACTUALLY DOES HERE
------------------------------------
There is no scheduled wake task in this project. Wake is detected inside the
app, by watching the wall clock jump between one-second ticks - the same
mechanism Dionysus uses. That was a deliberate choice, and it has one
consequence worth being plain about: with the window CLOSED, a wake does not
refresh anything. The next logon does. `--reason wake` is still accepted here
so the app can hand a wake off to this script, and so a scheduled task can be
added later without changing anything else.

Idempotent: the lock means a logon and a wake arriving together produce one
scan, and the single-instance socket means they produce one window.

Usage:
    pythonw jostocks_autorun.py --reason logon
    pythonw jostocks_autorun.py --reason wake
    python  jostocks_autorun.py --reason manual --verbose
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import logging.handlers
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import platform_support as platform               # noqa: E402

from jos import pipeline as pipe                  # noqa: E402
from jos import refresh as refresh_module         # noqa: E402
from jos import state as state_module             # noqa: E402
from jos.instance import send_to_running          # noqa: E402
from jos.paths import DataPaths, LOG_DIR, pipeline_data_dir  # noqa: E402

LOG_NAME = "autorun.log"
LOG_BYTES = 512 * 1024
LOG_KEEP = 3

log = logging.getLogger("jostocks.autorun")


def setup_logging(verbose=False):
    """Rotating log in data/logs (section 8).

    Under pythonw there is no console, so the log is the only record of what a
    logon run did. Rotating, because this runs every time the machine starts.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)

    handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / LOG_NAME, maxBytes=LOG_BYTES, backupCount=LOG_KEEP,
        encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)

    if verbose and sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("  %(message)s"))
        log.addHandler(console)


def open_window(settings) -> bool:
    """Start the app, or raise the one already running.

    sys.executable, never the `py` launcher - section 9b. Under pythonw that
    is pythonw, which is what a window should be started with anyway.
    """
    if send_to_running("refresh-finished"):
        log.info("the window was already open; told it to reload")
        return True

    script = PROJECT_DIR / "jostocks.py"
    if not script.is_file():
        log.error("jostocks.py is missing from %s", PROJECT_DIR)
        return False

    try:
        subprocess.Popen(
            [platform.python_executable(), str(script)],
            cwd=str(PROJECT_DIR), **platform.no_window_kwargs())
    except Exception as exc:                        # noqa: BLE001
        log.error("could not start the window: %s: %s", type(exc).__name__, exc)
        return False
    log.info("opened the window")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the jostocks data, and open the window at logon.")
    parser.add_argument("--reason", default="manual",
                        choices=("logon", "wake", "manual"),
                        help="what triggered this run")
    parser.add_argument("--force", action="store_true",
                        help="ignore min_minutes_between_scans")
    parser.add_argument("--no-open", action="store_true",
                        help="never open the window, whatever the settings say")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="also log to stdout")
    args = parser.parse_args(argv)

    state_module.init()
    setup_logging(args.verbose)

    started = dt.datetime.now()
    log.info("--- autorun start (reason=%s, pid=%s) ---", args.reason,
             os.getpid())

    settings = state_module.Settings()
    pipe.bootstrap(settings.get("pipeline_dir"))
    if not pipe.ready():
        log.warning("the stocks pipeline could not be imported: %s",
                    pipe.IMPORT_ERROR)

    data_paths = DataPaths(pipeline_data_dir(settings.get("data_dir")))

    # ── should this trigger refresh at all? ─────────────────────────
    wanted = {
        "logon": settings.get("auto_refresh_on_logon", True),
        "wake": settings.get("auto_refresh_on_wake", True),
        "manual": True,
    }[args.reason]

    ok, message = True, "no refresh was requested for this trigger"
    if wanted:
        ok, message = refresh_module.refresh(
            settings, data_paths, reason=args.reason, manual=args.force,
            on_state=lambda state, stage, error: log.info(
                "status: %s%s%s", state,
                f" ({stage})" if stage else "",
                f" - {error}" if error else ""))
        log.info("refresh %s: %s", "ok" if ok else "failed", message)
    else:
        log.info("auto refresh is off for reason=%s", args.reason)

    # ── open or notify the window ───────────────────────────────────
    should_open = {
        "logon": settings.get("open_window_on_logon", True),
        "wake": settings.get("open_window_on_wake", False),
        "manual": False,
    }[args.reason]

    if args.no_open:
        should_open = False

    if should_open:
        open_window(settings)
    elif ok and wanted:
        # Do not pop a window up, but a window that is already open should
        # show the new data rather than yesterday's.
        if send_to_running("refresh-finished"):
            log.info("told the running window to reload")

    elapsed = (dt.datetime.now() - started).total_seconds()
    log.info("--- autorun done in %.1fs ---", elapsed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
