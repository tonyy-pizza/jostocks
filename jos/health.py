r"""jos/health.py - is each holding's thesis still true?

Section 10 settles the rules, and they are triage flags, not sell signals.
Against the snapshot closest to `health_lookback_days` (30) ago:

    review   composite down by health_review_drop (1.5) or more, OR the trend
             flag has turned to failing, OR the debt flag has turned negative,
             OR the composite is now below health_red_below (5.0)
    watch    none of the above, but composite down between health_watch_drop
             (0.7) and health_review_drop, OR the composite crossed down out
             of green into yellow
    intact   otherwise

Until a snapshot at least `health_min_history_days` (14) old exists there is
nothing to compare against, so the status is "building history" rather than a
verdict invented from one data point. Every status carries the exact rule that
fired, for the tooltip.

HOW THIS DIFFERS FROM THE PIPELINE'S OWN EXIT REVIEW
----------------------------------------------------
position_sizer already re-scores every holding each run and writes a verdict
into sized_candidates.json as `holdings_review`. It is not the same question.
Its baseline is `latest_archive_scores()` - the most recent archived run,
which may be yesterday - so it answers "has this changed since the last scan".
This module's baseline is a month ago, so it answers "has this drifted over a
month". A holding that loses 0.3 a week for five weeks never trips the
pipeline's EXIT_DROP of 1.5 between consecutive runs, and is down 1.5 here.

Both are worth seeing, so the view shows this status and keeps the pipeline's
verdict beside it rather than picking a winner.

The green threshold defaults to the evaluator's own COLOUR_GOOD rather than
the brief's 7.5: v5.5 rescaled the composite and moved green to 6.25, so a
literal 7.5 would call almost everything "crossed out of green". It stays
overridable through `health_green_from` in settings.json.
"""

from __future__ import annotations

from . import pipeline as pipe

#: The three verdicts, plus the two states that are not verdicts at all.
REVIEW, WATCH, INTACT, BUILDING = "review", "watch", "intact", "building history"

#: No current composite, so nothing was measured. Distinct from "intact",
#: which is a claim that the thesis still holds - a claim nothing supports
#: when the name could not be scored. The evaluator takes the same care:
#: build_scores() suppresses a composite below its coverage minimum rather
#: than reporting a name as mediocre.
NO_DATA = "no data"

#: Trend and debt states that count as having turned against the holding.
TREND_FAILING = "deteriorating"
DEBT_NEGATIVE = "increasing"


def thresholds(settings) -> dict:
    """The rule parameters, from settings.json with the brief's defaults."""
    return {
        "review_drop": float(settings.get("health_review_drop", 1.5)),
        "watch_drop": float(settings.get("health_watch_drop", 0.7)),
        "lookback_days": float(settings.get("health_lookback_days", 30)),
        "min_history_days": float(settings.get("health_min_history_days", 14)),
        "red_below": float(settings.get("health_red_below", 5.0)),
        "green_from": float(settings.green_from()),
    }


def describe_rules(limits) -> str:
    """The whole rule set as one tooltip, so the table can explain itself."""
    return (
        f"Triage flags, not sell signals. Compared against the snapshot "
        f"closest to {limits['lookback_days']:.0f} days ago:\n\n"
        f"review   composite down {limits['review_drop']:.1f} or more, or the "
        f"trend turned failing, or debt turned rising, or the composite is "
        f"now below {limits['red_below']:.2f}\n"
        f"watch    composite down {limits['watch_drop']:.1f} to "
        f"{limits['review_drop']:.1f}, or it crossed down out of green "
        f"(≥ {limits['green_from']:.2f})\n"
        f"intact   none of the above\n\n"
        f"Until a snapshot {limits['min_history_days']:.0f} days old exists "
        f"there is nothing to compare against, and the status reads "
        f"“{BUILDING}”."
    )


def status_for(now, before, limits, history_days=None) -> dict:
    """One holding's status. `now` and `before` are candidate-ish records.

    Returns {status, reasons, delta, before, after}. `reasons` names the rule
    that fired, in the order the rules are checked, so the first line is the
    one that decided it.
    """
    after_score = pipe.num((now or {}).get("composite"))
    before_score = pipe.num((before or {}).get("composite"))
    delta = (None if after_score is None or before_score is None
             else round(after_score - before_score, 2))

    enough_history = (history_days is not None
                      and history_days >= limits["min_history_days"])

    # Nothing measured now. Every rule below tests the current composite or a
    # flag that comes with it, so none of them can fire - and falling through
    # to "intact" would assert the thesis still holds on the strength of no
    # evidence at all. The pipeline says "unavailable" for this; so do we.
    if after_score is None:
        reasons = ["could not be scored"]
        if not (now or {}):
            reasons.append("no record for this holding in the scan")
        else:
            reasons.append("the evaluator returned no composite - usually too "
                           "little data to rank, or the name is not in the "
                           "scanned universe")
        reasons.append("press re-score to try again")
        return {"status": NO_DATA, "reasons": reasons, "delta": None,
                "before": before_score, "after": None}

    # The red-band and trend rules need no history, but reporting a verdict
    # built from only one of three available tests would be a different rule
    # than the one documented. So history gates the status, and what is known
    # is still shown as a reason.
    if not enough_history or before is None or before_score is None:
        reasons = [f"composite {after_score:.2f} now"]
        if before_score is None and before is not None:
            reasons.append("no earlier composite for this name to compare "
                           "against")
        elif history_days is None:
            reasons.append("no earlier snapshot on file yet")
        else:
            reasons.append(
                f"history is {history_days:.0f} day(s) deep; needs "
                f"{limits['min_history_days']:.0f}")
        return {"status": BUILDING, "reasons": reasons, "delta": delta,
                "before": before_score, "after": after_score}

    reasons = []

    # ── review ──────────────────────────────────────────────────────
    if delta is not None and delta <= -limits["review_drop"]:
        reasons.append(f"composite down {abs(delta):.2f} since "
                       f"{limits['lookback_days']:.0f} days ago")

    was_trend = (before or {}).get("roa_trend")
    now_trend = (now or {}).get("roa_trend")
    if now_trend == TREND_FAILING and was_trend != TREND_FAILING:
        reasons.append("trend has turned to failing")

    was_debt = (before or {}).get("debt_trend")
    now_debt = (now or {}).get("debt_trend")
    if now_debt == DEBT_NEGATIVE and was_debt != DEBT_NEGATIVE:
        reasons.append("debt has turned to rising")

    if after_score is not None and after_score < limits["red_below"]:
        reasons.append(f"composite {after_score:.2f} is below "
                       f"{limits['red_below']:.2f}")

    if reasons:
        return {"status": REVIEW, "reasons": reasons, "delta": delta,
                "before": before_score, "after": after_score}

    # ── watch ───────────────────────────────────────────────────────
    if delta is not None and -limits["review_drop"] < delta <= -limits["watch_drop"]:
        reasons.append(f"composite down {abs(delta):.2f} since "
                       f"{limits['lookback_days']:.0f} days ago")

    if (before_score is not None and after_score is not None
            and before_score >= limits["green_from"]
            and limits["red_below"] <= after_score < limits["green_from"]):
        reasons.append(f"crossed down out of green "
                       f"({before_score:.2f} → {after_score:.2f})")

    if reasons:
        return {"status": WATCH, "reasons": reasons, "delta": delta,
                "before": before_score, "after": after_score}

    # ── intact ──────────────────────────────────────────────────────
    if delta is not None and delta > 0:
        reasons.append(f"composite up {delta:.2f} since "
                       f"{limits['lookback_days']:.0f} days ago")
    else:
        reasons.append("no rule fired")
    return {"status": INTACT, "reasons": reasons, "delta": delta,
            "before": before_score, "after": after_score}


def baseline_from(snapshot) -> dict:
    """{ticker: record} from a history snapshot, candidates and holdings both.

    A holding is usually in `candidates` too (the pipeline sizes it and marks
    it already_held), but a name sold out of the candidate list still appears
    in `holdings_review`, which carries the composite and the trend flags this
    module needs. Both are read, candidates winning, so a holding can be
    compared whichever list it was in a month ago.
    """
    out = {}
    for entry in ((snapshot or {}).get("holdings_review") or []):
        ticker = entry.get("ticker")
        if ticker:
            out[ticker] = entry
    for candidate in ((snapshot or {}).get("candidates") or []):
        ticker = candidate.get("ticker")
        if ticker:
            out[ticker] = candidate
    return out


def assess(holdings, current_records, baseline, limits, history_days=None,
           earnings=None) -> list:
    """Status for every holding. Returns rows ready for the table.

    `current_records` is {ticker: record} for now - from the fresh re-score
    where there is one, else from the scan. `baseline` is the same shape, a
    month back. `earnings` is {ticker: date-ish or None}.
    """
    rows = []
    for holding in holdings or ():
        ticker = holding.get("ticker")
        if not ticker:
            continue
        now = (current_records or {}).get(ticker) or {}
        before = (baseline or {}).get(ticker)
        verdict = status_for(now, before, limits, history_days=history_days)
        rows.append({
            "ticker": ticker,
            "holding": holding,
            "name": now.get("name"),
            "record": now,
            "next_report": (earnings or {}).get(ticker),
            **verdict,
        })
    return rows
