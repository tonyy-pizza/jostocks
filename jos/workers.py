r"""jos/workers.py - everything slow, off the UI thread.

Section 2 is unambiguous: network calls, scoring a ticker, re-scoring
holdings and subprocesses all run off the UI thread, and the UI must never
freeze. The pattern here is QThreadPool + QRunnable with a signal object,
which suits one-shot jobs better than a QThread subclass per task.

Two rules every job in here follows:

  - It never touches a widget. Results come back as a signal the main thread
    connects to, because a Qt widget may only be used from the thread that
    made it.
  - It never raises into the pool. An exception becomes a `failed` signal
    carrying the reason, so a bad ticker or a dropped connection shows up as a
    message in the panel it belongs to rather than a silent dead end. The
    pipeline makes this easy: score_candidate returns (None, reason) and
    market_data returns None on network failure, neither raising.
"""

from __future__ import annotations

import traceback

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from . import pipeline as pipe


class JobSignals(QObject):
    """Signals for one job. Separate because QRunnable is not a QObject."""

    done = pyqtSignal(object)        # the result
    failed = pyqtSignal(str)         # a reason, already human-readable
    progress = pyqtSignal(str)       # a line for a "working..." state


class Job(QRunnable):
    """Run `function` in the pool and report back on the main thread.

    `function` may take a `progress` keyword, which it can call with a string
    to update the UI while it works - used by the correlation job, which has
    several slow steps worth naming.
    """

    def __init__(self, function, *args, **kwargs):
        super().__init__()
        self.signals = JobSignals()
        self._function = function
        self._args = args
        self._kwargs = kwargs
        self._cancelled = False
        # Qt owns the QRunnable, but Python must not collect the Job (and with
        # it `signals`) while the pool thread is still inside run().
        self.setAutoDelete(False)

    def run(self):
        try:
            import inspect
            if "progress" in inspect.signature(self._function).parameters:
                self._kwargs["progress"] = self._emit_progress
            result = self._function(*self._args, **self._kwargs)
        except Exception as exc:                    # noqa: BLE001
            # The traceback goes to the log; the panel gets the short form.
            traceback.print_exc()
            self._emit("failed", f"{type(exc).__name__}: {exc}")
            return
        self._emit("done", result)

    def _emit_progress(self, text):
        self._emit("progress", text)

    def _emit(self, name, payload):
        """Emit a signal, unless the receiving side has already gone.

        A job can still be running when the window closes - the correlation
        matrix takes seconds, and a price fetch waits on the network. Qt then
        destroys the C++ half of `signals` while this thread is between
        statements, and emitting raises RuntimeError from inside a pool
        thread, where nothing can catch it. The job's result is worthless at
        that point anyway: there is no window left to show it in.
        """
        if self._cancelled:
            return
        try:
            getattr(self.signals, name).emit(payload)
        except RuntimeError:
            self._cancelled = True

    def cancel(self):
        """Stop this job reporting back. It still runs to completion."""
        self._cancelled = True


def pool() -> QThreadPool:
    """The shared pool.

    Capped low on purpose. These jobs are mostly waiting on Yahoo, and the
    pipeline's whole caching and backoff policy assumes one well-behaved
    client; eight parallel scoring jobs would be a different client.
    """
    instance = QThreadPool.globalInstance()
    if instance.maxThreadCount() > 3:
        instance.setMaxThreadCount(3)
    return instance


#: Jobs that have been queued and not yet reported back. Holding a reference
#: keeps Python from collecting a Job mid-run; the callbacks drop it again.
_live = set()


def submit(function, *args, on_done=None, on_failed=None, on_progress=None,
           **kwargs) -> Job:
    """Queue a job and wire its signals. Returns the Job, already running."""
    job = Job(function, *args, **kwargs)
    _live.add(job)

    def _retire(*_args):
        _live.discard(job)

    if on_done is not None:
        job.signals.done.connect(on_done)
    if on_failed is not None:
        job.signals.failed.connect(on_failed)
    if on_progress is not None:
        job.signals.progress.connect(on_progress)
    job.signals.done.connect(_retire)
    job.signals.failed.connect(_retire)

    pool().start(job)
    return job


def shutdown(wait_ms=2000):
    """Stop jobs reporting back, and give the pool a moment to drain.

    Called when the window closes. Cancelling first means a job that finishes
    during teardown discards its result instead of emitting into a widget
    that is already gone; the wait is a courtesy so a job that is nearly done
    can finish rather than being abandoned mid-write.
    """
    for job in list(_live):
        job.cancel()
    _live.clear()
    pool().waitForDone(int(wait_ms))


# ─── the jobs themselves ─────────────────────────────────────────────────
# Plain functions, so each is testable without Qt.

def score_ticker(ticker, account_size=None, force_refresh=False):
    """Score one ticker through the evaluator's own single-ticker entry point.

    score_candidate() is what stock_evaluator's batch mode uses per name, so
    this is the same scoring path the scan took - not a second one. It returns
    (record, reason) and never raises, so a bad symbol is a message.
    """
    pipe.require()
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        return None, "no symbol given"
    return pipe.se.score_candidate(symbol, account_size=account_size,
                                   force_refresh=force_refresh)


#: Suffixes to try for a bare Canadian symbol, in the order the pipeline
#: lists them (stocks_common.CAD_SUFFIXES): Toronto, Venture, CSE, Cboe
#: Canada. Used by the lookup box in Phase 2 and the watchlist in Phase 3.
CAD_SUFFIX_ORDER = (".TO", ".V", ".CN", ".NE")


def resolve_symbol(ticker, progress=None):
    """Find which listing a bare symbol means. Returns a list of candidates.

    "SHOP" could be the US line or Toronto. Rather than guessing, every
    listing that returns price history is reported so the caller can ask.
    Price history, not info: info comes back for symbols that do not trade.
    """
    pipe.require()
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        return []

    attempts = [symbol]
    if not any(symbol.endswith(s) for s in CAD_SUFFIX_ORDER):
        attempts += [symbol + suffix for suffix in CAD_SUFFIX_ORDER]

    found = []
    for attempt in attempts:
        if progress:
            progress(f"checking {attempt}...")
        rows = pipe.md.get_price_history(attempt, period="1mo")
        if not rows:
            continue
        info = pipe.md.get_info(attempt) or {}
        found.append({
            "ticker": attempt,
            "name": pipe.md.get_company_name(attempt, info),
            "currency": info.get("currency"),
            "last_close": rows[-1].get("close") if rows else None,
        })
    return found


def price_history(ticker, period="6mo"):
    """Closes for a sparkline. 24 weeks is about 6 months (section 6)."""
    pipe.require()
    rows = pipe.md.get_price_history(str(ticker or "").strip().upper(),
                                     period=period)
    return rows or []


def rescore_holdings(holdings, scored_records=None, progress=None):
    """Re-run every holding through stage 1, via the pipeline's own reviewer.

    position_sizer.review_holdings() is the function the scan itself uses for
    its exit review, so the verdicts here are the same ones the terminal
    report shows. Reusing it also means the review rules - which thresholds,
    which trend categories, how many have to be deteriorating - stay in one
    place and keep meaning the same thing after a recalibration.
    """
    pipe.require()
    if progress:
        progress(f"re-scoring {len(holdings or ())} holding(s)...")
    previous, _run = pipe.ps.latest_archive_scores()
    return pipe.ps.review_holdings(list(holdings or ()),
                                   scored_records=scored_records or {},
                                   previous=previous, quiet=True)
