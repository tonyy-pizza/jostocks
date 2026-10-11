#!/usr/bin/env python3
"""Build dated history snapshots so the Phase 2 views have a real baseline.

Takes the fixture scan from make_fixtures.py and writes backdated copies into
data/history/ with plausibly drifted scores, so that:

  - "changes since last scan" has a previous scan to diff against, with real
    new / up / down / dropped rows,
  - holdings health has a snapshot ~30 days old to compare against, and
    enough history depth to produce a status rather than "building history".

Usage:
    python tests/make_history.py tests/fakedata
"""
import datetime as dt
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jos import pipeline as pipe
from jos import state as state_module
from jos.paths import HISTORY_DIR

assert pipe.bootstrap(), pipe.IMPORT_ERROR

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fakedata")
current = json.loads((DATA / "sized_candidates.json").read_text())
random.seed(7)

HISTORY_DIR.mkdir(parents=True, exist_ok=True)
for stale in HISTORY_DIR.glob("*.json"):
    stale.unlink()

NOW = dt.datetime.now()


def drifted(document, days_ago, seed):
    """A backdated copy with the scores moved, names added and dropped."""
    rng = random.Random(seed)
    copy = json.loads(json.dumps(document))
    when = NOW - dt.timedelta(days=days_ago)
    copy["generated_at"] = when.isoformat(timespec="seconds")

    candidates = copy.get("candidates") or []

    # Drop two names, so the current scan shows them as "new".
    kept = [c for c in candidates if c["ticker"] not in ("AMD", "SU.TO")]

    # Invent one name that is gone now, so the current scan shows "dropped".
    ghost = json.loads(json.dumps(kept[0]))
    ghost.update({"ticker": "GONE.TO", "name": "Delisted Holdings Ltd",
                  "sector": "Industrials", "composite": 6.90,
                  "already_held": False, "holding": None, "cluster": None})
    kept.append(ghost)

    for candidate in kept:
        base = candidate.get("composite")
        if base is None:
            continue
        # Older snapshots sit further from today's score.
        swing = rng.uniform(-0.5, 0.5) + (days_ago / 30.0) * rng.uniform(-1.2, 1.2)
        candidate["composite"] = round(max(0.0, min(10.0, base + swing)), 2)

        # Flip a couple of flags so the "why" column has real causes to name.
        if candidate["ticker"] == "BNS.TO":
            candidate["roa_trend"] = "flat"          # now deteriorating
            candidate["divergence_pattern"] = "neutral"
        if candidate["ticker"] == "XYZ.V":
            candidate["liquidity_flag"] = False      # now flagged
        if candidate["ticker"] == "NVDA":
            sizing = candidate.get("sizing") or {}
            sizing["conviction"] = "not_applicable"
        if candidate["ticker"] == "TD.TO" and days_ago > 20:
            # A holding sliding over a month: a month ago it scored well
            # above the review threshold, so the 30-day rule should fire
            # where the pipeline's run-to-run comparison would not.
            candidate["composite"] = round(min(10.0, base + 2.2), 2)
            candidate["roa_trend"] = "improving"

    copy["candidates"] = kept

    for review in (copy.get("holdings_review") or []):
        base = review.get("composite")
        if base is None:
            continue
        swing = rng.uniform(-0.3, 0.3) + (days_ago / 30.0) * rng.uniform(-1.0, 0.6)
        review["composite"] = round(max(0.0, min(10.0, base + swing)), 2)
        if review["ticker"] == "AAPL" and days_ago > 20:
            review["composite"] = round(min(10.0, (base or 5) + 0.9), 2)
    return copy


# Oldest first, so history_days() measures the full span.
plan = [(45, 101), (30, 202), (14, 303), (7, 404), (1, 505)]
written = []
for days_ago, seed in plan:
    document = drifted(current, days_ago, seed)
    name = (NOW - dt.timedelta(days=days_ago)).strftime(
        state_module.SNAPSHOT_FORMAT)
    path = HISTORY_DIR / f"{name}.json"
    pipe.common.write_json(document, path, default=pipe.common.json_default)
    written.append((days_ago, path.name, len(document["candidates"])))

print(f"wrote {len(written)} snapshots to {HISTORY_DIR}")
for days_ago, name, count in written:
    print(f"  {name}  ({days_ago:>2}d ago, {count} candidates)")
print(f"history depth: {state_module.history_days():.0f} days")
