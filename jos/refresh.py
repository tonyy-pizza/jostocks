r"""jos/refresh.py - running a scan, from inside the app.

The app and the autorun need the same behaviour: take a lock so two scans
cannot overlap, wait for the network, skip the run if the data is fresh
enough, then run scan_report.py at below-normal priority with no console
window. So the logic lives here, in functions with no Qt in them, and both
callers use it - `RefreshController` wraps it in signals for the window,
and jostocks_autorun.py calls the same functions directly.

The scan itself is never reimplemented. scan_report.py already skips stages
whose outputs are fresh, re-runs everything downstream of a stage that ran,
and enforces market_data's caching and backoff. Section 9 is explicit: do not
bypass either, and do not force a full refresh. So the subprocess gets the
arguments from settings (--include-canada by default for this account) plus
--quiet, and nothing else.

Exit codes, from scan_report's own main():

    0   every stage that ran, ran
    1   a required stage failed; the report was rendered from the files
        already on disk, which are older than this run
    2   there was no sized_candidates.json to render at all

1 is reported as a partial success, because the data on screen is still the
last good scan and saying "failed" would imply otherwise.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal

import platform_support as platform

from . import pipeline as pipe
from . import state as state_module
from .datastore import age_seconds
from .paths import REFRESH_LOCK_PATH

#: A lock older than this is assumed to be from a process that died.
STALE_LOCK_SECONDS = 2 * 60 * 60

#: How long to wait between connectivity probes while waiting for a network.
NETWORK_PROBE_SECONDS = 10

#: Hard ceiling on one scan.
SCAN_TIMEOUT_SECONDS = 90 * 60


# ─── the lock ────────────────────────────────────────────────────────────

def read_lock(path=None) -> dict:
    document = state_module.read_json(path or REFRESH_LOCK_PATH)
    return document if isinstance(document, dict) else {}


def lock_is_live(path=None) -> bool:
    """Whether a refresh is genuinely running somewhere.

    A lock file alone is not evidence: the process may have been killed, and
    section 9's test plan requires that case not to wedge the app forever. So
    the PID is checked where the OS allows it, and any lock past
    STALE_LOCK_SECONDS is treated as dead regardless.
    """
    lock = read_lock(path)
    if not lock:
        return False

    stamp = lock.get("started_at")
    age = age_seconds(stamp)
    if age is not None and age > STALE_LOCK_SECONDS:
        return False

    pid = lock.get("pid")
    if not isinstance(pid, int):
        return age is not None and age <= STALE_LOCK_SECONDS

    if pid == os.getpid():
        return True

    try:
        if hasattr(os, "kill"):
            os.kill(pid, 0)              # signal 0: does it exist
            return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                      # someone else's: it exists
    except OSError:
        pass

    return age is not None and age <= STALE_LOCK_SECONDS


def take_lock(reason="manual", path=None) -> bool:
    """Claim the refresh lock. False if someone else already has it."""
    path = Path(path or REFRESH_LOCK_PATH)
    if lock_is_live(path):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    return state_module.write_json(
        {"pid": os.getpid(), "reason": reason,
         "started_at": dt.datetime.now().isoformat(timespec="seconds")}, path)


def release_lock(path=None) -> None:
    try:
        Path(path or REFRESH_LOCK_PATH).unlink()
    except OSError:
        pass


# ─── the decision ────────────────────────────────────────────────────────

def data_is_fresh(data_paths, minutes) -> bool:
    """Whether the last scan is recent enough to skip this one.

    Judged on generated_at inside sized_candidates.json rather than the
    file's mtime, because the pipeline rewrites that file on every run - even
    the runs where every stage was fresh and nothing changed.
    """
    if not minutes:
        return False
    document = state_module.read_json(data_paths.sized)
    if not isinstance(document, dict):
        return False
    age = age_seconds(document.get("generated_at"))
    return age is not None and age < float(minutes) * 60


def wait_for_network(minutes, on_tick=None) -> bool:
    """Poll for connectivity. True as soon as there is some.

    A wake fires before Wi-Fi is back, so this is the difference between a
    refresh that works after sleep and one that reports a failure every time.
    """
    deadline = time.monotonic() + float(minutes or 0) * 60
    attempt = 0
    while True:
        if platform.network_ready():
            return True
        if time.monotonic() >= deadline:
            return False
        attempt += 1
        if on_tick:
            on_tick(attempt)
        time.sleep(min(NETWORK_PROBE_SECONDS * attempt, 60))


# ─── the scan ────────────────────────────────────────────────────────────

def run_scan(scan_args=None, timeout=SCAN_TIMEOUT_SECONDS, cwd=None):
    """Run scan_report.py as a subprocess. Returns (ok, detail).

    sys.executable, never `py` or `pythonw`: section 9b forbids app code from
    naming either, and the launcher is not guaranteed to resolve to the
    environment this app is running in.
    """
    script = pipe.scan_report_script()
    if script is None:
        return False, ("scan_report.py not found - "
                       f"{pipe.IMPORT_ERROR or 'the pipeline is not beside this project'}")

    command = [platform.python_executable(), str(script)]
    command += [str(a) for a in (scan_args or [])]
    if "--quiet" not in command:
        command.append("--quiet")

    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            cwd=str(cwd or script.parent),
            **platform.lower_priority())
    except subprocess.TimeoutExpired:
        return False, f"the scan did not finish within {timeout // 60} minutes"
    except Exception as exc:                        # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"

    if done.returncode == 0:
        return True, "scan finished"
    if done.returncode == 1:
        # A required stage failed. The files on disk are the last good scan.
        tail = (done.stdout or done.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else "a required stage failed"
        return False, f"the scan did not complete: {detail}"
    if done.returncode == 2:
        return False, ("the scan has nothing to render yet - "
                       "no sized_candidates.json was produced")
    tail = (done.stderr or done.stdout or "").strip()[-200:]
    return False, f"scan exited {done.returncode}: {tail}"


def refresh(settings, data_paths, reason="manual", manual=False,
            on_state=None):
    """One whole refresh: lock, network, freshness, scan, snapshot.

    Returns (ok, message). Every step is fail-soft and writes status.json at
    each transition, so the app's chip can describe what is happening without
    this function knowing anything about Qt.
    """
    def _state(state, stage=None, error=None):
        state_module.write_status(state, stage=stage, last_error=error,
                                  reason=reason)
        if on_state:
            on_state(state, stage, error)

    if not settings.get("scan_enabled", True):
        _state("idle", stage="scanning is off on this machine")
        return False, ("scan_enabled is false in settings.json - this machine "
                       "reads the data but never runs the pipeline")

    if not take_lock(reason):
        return False, "a refresh is already running"

    try:
        minutes = settings.get("min_minutes_between_scans", 60)
        if not manual and data_is_fresh(data_paths, minutes):
            _state("idle", stage="data already fresh")
            return True, (f"the data is newer than {minutes} minutes, "
                          f"so no scan ran")

        _state("waiting_for_network")
        if not wait_for_network(settings.get("network_wait_minutes", 5)):
            _state("failed", error="no network")
            return False, ("there is still no network, so the existing data "
                           "was left alone")

        _state("refreshing", stage="scan_report")
        ok, detail = run_scan(settings.scan_args())
        if not ok:
            _state("failed", error=detail)
            return False, detail

        # Post-steps, each fail-soft: one failing must not block the others.
        _state("refreshing", stage="snapshot")
        try:
            document = state_module.read_json(data_paths.sized)
            if document:
                state_module.take_snapshot(
                    document, keep_days=settings.get("history_keep_days", 90))
        except Exception:                           # noqa: BLE001
            pass

        _state("idle")
        return True, detail
    finally:
        release_lock()


# ─── the Qt wrapper ──────────────────────────────────────────────────────

class _RefreshThread(QThread):
    """Runs one refresh. A thread, not the pool: it is long and exclusive."""

    state = pyqtSignal(str, object, object)
    done = pyqtSignal(bool, str)

    def __init__(self, settings, data_paths, reason, manual, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._paths = data_paths
        self._reason = reason
        self._manual = manual

    def run(self):
        ok, message = refresh(
            self._settings, self._paths, reason=self._reason,
            manual=self._manual,
            on_state=lambda s, stage, error: self.state.emit(s, stage, error))
        self.done.emit(ok, message)


class RefreshController(QObject):
    """The app's view of refreshing: start one, hear about the state."""

    status_changed = pyqtSignal(dict)
    finished = pyqtSignal(bool, str)

    def __init__(self, settings, data_paths, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._paths = data_paths
        self._thread = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def status(self) -> dict:
        """The status document the autorun and the app share."""
        return state_module.read_status()

    def start(self, reason="manual", manual=False) -> bool:
        """Begin a refresh unless one is already going."""
        if self.running():
            return False
        if lock_is_live():
            # Another process - the autorun, most likely - has it.
            self.status_changed.emit(self.status())
            return False

        self._thread = _RefreshThread(self._settings, self._paths, reason,
                                      manual, self)
        self._thread.state.connect(self._on_state)
        self._thread.done.connect(self._on_done)
        self._thread.start()
        return True

    def _on_state(self, _state, _stage, _error):
        self.status_changed.emit(self.status())

    def _on_done(self, ok, message):
        self.status_changed.emit(self.status())
        self.finished.emit(ok, message)
        self._thread = None
