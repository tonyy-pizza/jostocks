r"""jos/views/status.py - the toolbar's freshness and refresh state.

One chip, four levels, and the text has to be true in every combination of
two independent facts: what the REFRESH is doing (the autorun's status.json)
and what the last SCAN did (the pipeline's run_status.json). They are not the
same question, and conflating them is how a chip ends up saying "up to date"
over data from a run that halted at stage two.

Priority, most urgent first:

  1. a refresh is running, or waiting for the network   -> busy
  2. the last refresh failed                            -> error, with the
                                                           date of the data
                                                           still on screen
  3. the last scan halted on a required stage           -> error
  4. the data is older than stale_warning_days          -> warn
  5. otherwise                                          -> ok

Section 9: never a modal dialog at startup. This is a label, and the detail
goes in its tooltip.

The stage count is read from run_status.json rather than assumed. The brief
says "stage 2 of 5"; the pipeline has seven stages (universe, evaluate,
sentiment, cluster, size, timing, exits), three of them optional, so a
hardcoded five would be wrong in both directions.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel

from .. import theme
from ..datastore import describe_age, age_seconds, parse_stamp


def _refresh_style(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class StatusChip(QLabel):
    """The refresh/freshness chip."""

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setObjectName("statusChip")
        self._level = "ok"

    def _set(self, text, level, tooltip):
        self.setText(text)
        self.setToolTip(tooltip)
        if level != self._level:
            self._level = level
            self.setProperty("level", level)
            _refresh_style(self)

    def update_state(self, scan, status, stale_days=3, wake_note=None):
        """Re-render from the autorun status and the scan on disk."""
        state = (status or {}).get("state") or "idle"
        stage = (status or {}).get("stage")
        error = (status or {}).get("last_error")

        age = scan.age if scan else None
        generated = parse_stamp(scan.generated_at) if scan else None
        # "Oct 5", without a platform-specific strftime flag: %-d is glibc
        # only and %#d is Windows only, so neither may appear here.
        when = (f"{generated:%b} {generated.day}") if generated else None

        lines = []
        if scan is not None:
            lines.append(f"data generated {describe_age(age)}"
                         + (f" ({when})" if when else ""))
            lines.append(scan.freshness_line())
            lines.append(f"coverage: {scan.coverage_line()}")

        # ── 1. busy ─────────────────────────────────────────────────
        if state == "waiting_for_network":
            self._set("waiting for network", "busy",
                      "\n".join(lines + [
                          "A wake fires before Wi-Fi is back, so the refresh "
                          "retries with backoff before giving up."]))
            return

        if state == "refreshing":
            label = f"refreshing: {stage}" if stage else "refreshing"
            total, index = self._stage_position(scan, stage)
            if total and index:
                label = f"refreshing: stage {index} of {total}"
                if stage:
                    label += f" ({stage})"
            self._set(label, "busy", "\n".join(lines + [
                "A scan is running. The window keeps showing the last good "
                "data until it finishes."]))
            return

        # ── 2. refresh failed ───────────────────────────────────────
        if state == "failed":
            label = "refresh failed"
            if when:
                label += f", showing data from {when}"
            self._set(label, "error", "\n".join(lines + [
                f"last error: {error or 'unknown'}",
                "The data on screen is the last good scan."]))
            return

        # ── 3. the scan itself halted ───────────────────────────────
        blocking = scan.blocking_stages() if scan else []
        if blocking:
            self._set(f"scan halted at {blocking[0]}", "error",
                      "\n".join(lines + [
                          "These stages failed: " + ", ".join(blocking),
                          "Every stage after a failed one reads what it was "
                          "supposed to write, so the files below it are from "
                          "an earlier run."]))
            return

        # ── 4. stale ────────────────────────────────────────────────
        if age is not None and age > float(stale_days) * 86400:
            label = f"data {describe_age(age)}"
            self._set(label, "warn", "\n".join(lines + [
                f"Older than the {stale_days}-day warning threshold "
                f"(stale_warning_days in settings.json)."]))
            return

        if scan is None or not scan.ok:
            self._set("no data yet", "warn",
                      (scan.empty_reason if scan else
                       "nothing has been loaded yet"))
            return

        # ── 5. fine ─────────────────────────────────────────────────
        tooltip = lines + []
        if wake_note:
            tooltip.append(wake_note)
        self._set("up to date", "ok", "\n".join(tooltip))

    @staticmethod
    def _stage_position(scan, stage):
        """(total, index) of a stage within the run, from run_status.json."""
        rows = scan.stage_rows() if scan else []
        if not rows:
            return None, None
        names = [row.get("stage") for row in rows]
        total = len(names)
        if stage in names:
            return total, names.index(stage) + 1
        return total, None
