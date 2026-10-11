r"""jos/changes.py - what moved between this scan and the last one.

Compares the current sized_candidates.json against the previous snapshot in
data/history/, and classifies every name into one of four rows: new, up, down,
dropped. A name whose composite barely moved is not a row at all - a table
listing forty names that each shifted by 0.02 is a table nobody reads.

The "why" is the point of this view, and it is the part that is easy to get
dishonest. Every reason below is read from a field that actually changed
between the two files. Where no field explains the move, it says the score
changed and nothing else - which is a real answer, because the composite is
built from metrics that are not all individually reported.

Dropped names are the hard case: the evidence is an absence. The scan's own
`params` say what the shortlist was filtered on, and scored_candidates.json
says whether the name was scored at all this time, so between them most
disappearances can be attributed:

    scored, but below params.min_composite   -> fell below the minimum
    scored, but outside params.top           -> pushed out of the top N
    scored, and neither of those             -> left the Stage 2 shortlist
    not scored at all                        -> left the universe

and when none of that holds, it says the name is no longer in the shortlist
rather than guessing at a cause.
"""

from __future__ import annotations

from . import pipeline as pipe

#: Composite moves smaller than this are not worth a row. The pipeline's own
#: clustering treats 0.5 as roughly the resolution of the composite
#: (rmt_cluster.MIN_WINNER_GAP), so a tenth of that is comfortably inside the
#: noise and safe to ignore.
MOVE_EPSILON = 0.05

#: The four kinds of row, in the order the filter chips show them.
KINDS = ("new", "up", "down", "dropped")

TREND_WORDS = {
    "improving": "improving",
    "flat": "flat",
    "mixed": "mixed",
    "deteriorating": "failing",
}

CONVICTION_WORDS = {
    "disconnect_supported": "disconnect, supported",
    "disconnect_unverified": "disconnect, unverified",
    "disconnect_contested": "disconnect, contested",
    "trap_confirmed": "value trap, confirmed",
    "trap_contested": "value trap, contested by hype",
    "trap_unverified": "value trap, unverified",
    "not_applicable": "neutral",
}


def _by_ticker(document) -> dict:
    """{ticker: candidate} from a sized_candidates document."""
    out = {}
    for candidate in ((document or {}).get("candidates") or []):
        ticker = candidate.get("ticker")
        if ticker:
            out[ticker] = candidate
    return out


def _reasons_for_move(before, after) -> list:
    """Why a name that is in both scans scores differently now.

    Only fields that genuinely differ between the two records produce a line,
    and each line names the new state rather than the delta, because "trend
    now failing" is actionable where "trend changed" is not.
    """
    reasons = []

    was, now = before.get("divergence_pattern"), after.get("divergence_pattern")
    if was != now and now:
        if now == "price_disconnect":
            reasons.append("pattern is now disconnect")
        elif now == "trend_confirms_decline":
            reasons.append("pattern is now value-trap")
        else:
            reasons.append("pattern is now neutral")

    was, now = before.get("roa_trend"), after.get("roa_trend")
    if was != now and now:
        reasons.append(f"trend now {TREND_WORDS.get(now, now)}")

    was = bool(before.get("liquidity_flag"))
    now = bool(after.get("liquidity_flag"))
    if now and not was:
        reasons.append("failed the liquidity filter")
    elif was and not now:
        reasons.append("liquidity now clears")

    was = (before.get("sizing") or {}).get("conviction")
    now = (after.get("sizing") or {}).get("conviction")
    if was != now and now:
        reasons.append(f"read is now {CONVICTION_WORDS.get(now, now)}")

    was, now = before.get("debt_trend"), after.get("debt_trend")
    if was != now and now == "increasing":
        reasons.append("debt rising over the window")

    was, now = before.get("rating"), after.get("rating")
    if was != now and now:
        reasons.append(f"rated {str(now).lower()}")

    # Cluster standing, which changes how the name should be read even when
    # its own score has not moved much.
    was = (before.get("cluster") or {}).get("is_winner")
    now = (after.get("cluster") or {}).get("is_winner")
    if now and not was:
        reasons.append("now the pick of its cluster")
    elif was and not now:
        reasons.append("no longer the pick of its cluster")

    return reasons


def _reason_dropped(ticker, before, params, scored_tickers, scored_record):
    """Why a name in the previous scan is not in this one.

    `scored_tickers` is what the evaluator scored this run; `scored_record` is
    that name's record if it has one. Between them and the scan's filters,
    most disappearances have a real explanation rather than a shrug.
    """
    minimum = pipe.num((params or {}).get("min_composite"))
    top = (params or {}).get("top")

    if scored_tickers and ticker not in scored_tickers:
        return ["no longer in the scanned universe"]

    composite = pipe.num((scored_record or {}).get("composite"))

    if composite is None and scored_record is not None:
        return ["scored, but its composite was suppressed for thin data"]

    if minimum is not None and composite is not None and composite < minimum:
        return [f"fell below the minimum composite ({composite:.2f} "
                f"under {minimum:.2f})"]

    if (scored_record or {}).get("liquidity_flag"):
        return ["failed the liquidity filter"]

    if top:
        return [f"pushed out of the top {top}"]

    if scored_record is not None:
        return ["left the Stage 2 shortlist - no longer clustered or standalone"]

    return ["no longer in the scan's shortlist"]


def compare(current, previous, scored=None) -> dict:
    """Diff two sized_candidates documents.

    Returns:
        {
          "available": bool,          # was there an earlier scan to compare?
          "reason": str,              # why not, when not
          "before_at": str | None,    # the previous scan's generated_at
          "after_at": str | None,
          "rows": [ {ticker, name, sector, kind, before, after, delta,
                     reasons, already_held} ],
          "counts": {kind: n},
        }

    `rows` is sorted with the biggest absolute move first, so the names worth
    looking at are at the top whichever filter is on.
    """
    after_map = _by_ticker(current)
    after_at = (current or {}).get("generated_at")

    if not isinstance(previous, dict) or not previous.get("candidates"):
        return {
            "available": False,
            "reason": ("no earlier scan to compare against yet - this view "
                       "fills in once a second scan has run and been "
                       "snapshotted"),
            "before_at": None, "after_at": after_at,
            "rows": [], "counts": {kind: 0 for kind in KINDS},
        }

    before_map = _by_ticker(previous)
    before_at = previous.get("generated_at")

    if before_at and after_at and before_at == after_at:
        return {
            "available": False,
            "reason": ("the snapshot on file is this same scan - nothing has "
                       "changed since it was taken"),
            "before_at": before_at, "after_at": after_at,
            "rows": [], "counts": {kind: 0 for kind in KINDS},
        }

    params = (current or {}).get("params") or {}
    scored_records = {}
    for record in ((scored or {}).get("scored") or []):
        ticker = record.get("ticker")
        if ticker:
            scored_records[ticker] = record
    scored_tickers = set(scored_records)

    rows = []

    for ticker, after in after_map.items():
        now = pipe.num(after.get("composite"))
        before = before_map.get(ticker)

        if before is None:
            rows.append({
                "ticker": ticker, "name": after.get("name"),
                "sector": after.get("sector"), "kind": "new",
                "before": None, "after": now, "delta": None,
                "already_held": bool(after.get("already_held")),
                "reasons": _new_reasons(after),
            })
            continue

        was = pipe.num(before.get("composite"))
        if was is None or now is None:
            delta = None
        else:
            delta = round(now - was, 2)

        if delta is None or abs(delta) < MOVE_EPSILON:
            continue                    # not a move worth a row

        rows.append({
            "ticker": ticker, "name": after.get("name"),
            "sector": after.get("sector"),
            "kind": "up" if delta > 0 else "down",
            "before": was, "after": now, "delta": delta,
            "already_held": bool(after.get("already_held")),
            "reasons": _reasons_for_move(before, after) or ["score moved"],
        })

    for ticker, before in before_map.items():
        if ticker in after_map:
            continue
        rows.append({
            "ticker": ticker, "name": before.get("name"),
            "sector": before.get("sector"), "kind": "dropped",
            "before": pipe.num(before.get("composite")), "after": None,
            "delta": None,
            "already_held": bool(before.get("already_held")),
            "reasons": _reason_dropped(ticker, before, params, scored_tickers,
                                       scored_records.get(ticker)),
        })

    def _sort_key(row):
        # Biggest mover first; new and dropped sort by the score they have.
        magnitude = abs(row["delta"]) if row["delta"] is not None else None
        if magnitude is None:
            magnitude = row["after"] if row["after"] is not None else row["before"]
            magnitude = (magnitude or 0) / 100.0      # below any real delta
        return -magnitude

    rows.sort(key=_sort_key)

    counts = {kind: sum(1 for r in rows if r["kind"] == kind) for kind in KINDS}
    return {"available": True, "reason": "", "before_at": before_at,
            "after_at": after_at, "rows": rows, "counts": counts}


def _new_reasons(candidate) -> list:
    """Why a name is worth noticing on its first appearance."""
    reasons = []
    rating = candidate.get("rating")
    if rating:
        reasons.append(f"new, rated {str(rating).lower()}")
    else:
        reasons.append("new in this scan")

    pattern = candidate.get("divergence_pattern")
    if pattern == "price_disconnect":
        reasons.append("price low, trend intact")
    elif pattern == "trend_confirms_decline":
        reasons.append("price low and the trend is deteriorating")

    cluster = candidate.get("cluster") or {}
    if cluster.get("is_winner"):
        reasons.append("the pick of its cluster")
    elif cluster.get("demoted"):
        reasons.append("demoted behind its cluster's pick")

    if candidate.get("liquidity_flag"):
        reasons.append("thin liquidity")
    return reasons
