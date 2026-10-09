#!/usr/bin/env python3
"""Build a data directory shaped exactly like the pipeline's real output.

Schemas taken from the pipeline source, not from the brief: position_sizer's
size_shortlist() for sized_candidates.json (including holdings_review and the
conviction keys), rmt_cluster's cluster_shortlist() for clustered.json,
stock_evaluator's score_candidate() for scored_candidates.json, and
scan_report's write_run_status() for run_status.json.
"""
import datetime as dt, json, os, random, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jos import pipeline as pipe
assert pipe.bootstrap(), pipe.IMPORT_ERROR
ps, se = pipe.ps, pipe.se

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "./fakedata")
OUT.mkdir(parents=True, exist_ok=True)
random.seed(11)

NOW = dt.datetime.now()
STAMP = NOW.isoformat(timespec="seconds")

HOLDINGS = [
    {"ticker": "RY.TO",  "shares": 120, "cost_basis": 118.40, "currency": "CAD",
     "entry_date": "2025-02-10", "entry_price": 115.00},
    {"ticker": "TD.TO",  "shares": 200, "cost_basis":  78.20, "currency": "CAD",
     "entry_date": "2025-04-02", "entry_price":  80.10},
    {"ticker": "AAPL",   "shares":  40, "cost_basis": 171.50, "currency": "USD",
     "entry_date": "2024-11-18", "entry_price": 168.90},
]

# (ticker, name, sector, currency, composite, held, divergence, liq_flag, roa_trend)
NAMES = [
    ("BMO.TO", "Bank of Montreal",        "Financial Services", "CAD", 8.42, False, "price_disconnect",        False, "improving"),
    ("CM.TO",  "Canadian Imperial Bank",  "Financial Services", "CAD", 8.05, False, "price_disconnect",        False, "flat"),
    ("NA.TO",  "National Bank of Canada", "Financial Services", "CAD", 7.61, False, "neutral",                 False, "improving"),
    ("BNS.TO", "Bank of Nova Scotia",     "Financial Services", "CAD", 6.88, False, "trend_confirms_decline",  False, "deteriorating"),
    ("MSFT",   "Microsoft Corporation",   "Technology",         "USD", 8.31, False, "neutral",                 False, "improving"),
    ("NVDA",   "NVIDIA Corporation",      "Technology",         "USD", 7.94, False, "neutral",                 False, "improving"),
    ("AMD",    "Advanced Micro Devices",  "Technology",         "USD", 6.42, False, "neutral",                 False, "mixed"),
    ("SHOP.TO","Shopify Inc.",            "Technology",         "CAD", 5.88, False, "price_disconnect",        False, "mixed"),
    ("CNQ.TO", "Canadian Natural Res.",   "Energy",             "CAD", 7.12, False, "neutral",                 False, "flat"),
    ("SU.TO",  "Suncor Energy",           "Energy",             "CAD", 6.55, False, "price_disconnect",        False, "mixed"),
    ("XYZ.V",  "Junior Venture Co",       "Basic Materials",    "CAD", 4.80, False, "trend_confirms_decline",  True,  "deteriorating"),
    ("AAPL",   "Apple Inc.",              "Technology",         "USD", 7.35, True,  "neutral",                 False, "flat"),
    ("RY.TO",  "Royal Bank of Canada",    "Financial Services", "CAD", 7.80, True,  "neutral",                 False, "improving"),
]

DIMS = ("Valuation", "Profitability", "Growth", "Health", "Momentum", "Frameworks")


def dims_for(composite):
    out = {}
    for name in DIMS:
        if random.random() < 0.08:
            out[name] = None          # a dimension Yahoo had nothing for
        else:
            out[name] = round(max(0.0, min(10.0, composite + random.uniform(-1.8, 1.8))), 2)
    return out


def metrics_for(ticker, sector, currency, composite):
    return {
        "name": None, "sector": sector, "industry": sector,
        "quote_currency": currency,
        "beta": round(random.uniform(0.7, 1.6), 2),
        "debt_eq": round(random.uniform(0.2, 1.5), 2),
        "pos_52w": round(random.uniform(0.05, 0.95), 3),
        "avg_volume": random.randint(300_000, 9_000_000),
        "data_coverage": round(random.uniform(0.6, 0.98), 3),
        "price": round(random.uniform(20, 400), 2),
        "growth_trajectory": random.choice(["steady", "decelerating", "accelerating"]),
        "margin_trend": random.choice(["stable", "compressing", "expanding"]),
        "warnings": [], "notes": [],
    }


scored, sentiment = [], {}
for ticker, name, sector, currency, composite, held, divergence, liq, roa in NAMES:
    dims = dims_for(composite)
    metrics = metrics_for(ticker, sector, currency, composite)
    metrics["name"] = name
    record = {
        "ticker": ticker, "name": name, "sector": sector, "industry": sector,
        "quote_currency": currency, "composite": composite,
        "rating": se.rate(composite, dims, metrics),
        "dims": dims,
        "coverage": round(random.uniform(0.6, 0.95), 3),
        "dim_coverage": {k: round(random.uniform(0.4, 1.0), 3) for k in DIMS},
        "score_notes": [],
        "metrics": metrics,
        "frameworks": {
            "piotroski": {"score": random.randint(3, 9), "max": 9,
                          "label": random.choice(["Strong", "Moderate", "Weak"]),
                          "signals": []},
            "altman_z": {"score": round(random.uniform(0.8, 5.2), 2),
                         "zone": random.choice(["Safe", "Grey", "Distress"]),
                         "warning": None},
            "graham": None, "magic_formula": None, "dcf": None,
        },
        "value_screen": None, "insider": None, "concentration": None,
        "data_coverage": metrics["data_coverage"],
        "trend_years_available": random.randint(1, 5),
        "roa_trend_consistent": roa in ("improving", "flat"),
        "roa_trend": roa,
        "fcf_positive_years": random.randint(0, 5),
        "debt_trend": random.choice(["increasing", "decreasing", "stable"]),
        "trend_detail": {"fcf_years_available": 5},
        "liquidity_flag": liq,
        "liquidity": {"position_pct_of_adv": round(random.uniform(0.001, 0.03), 4)},
        "divergence_pattern": divergence,
        "divergence_detail": {"reason": f"{divergence} on the trend data"},
        "financials_as_of": (NOW - dt.timedelta(days=random.randint(1, 120))).date().isoformat(),
        "price_as_of": (NOW - dt.timedelta(hours=random.randint(1, 20))).isoformat(timespec="seconds"),
        "warnings": (["Thin liquidity: a 3% position would be 4.20% of average "
                      "daily dollar volume (flag above 1%)."] if liq else []),
        "notes": [],
    }
    scored.append(record)
    if random.random() < 0.7:
        sentiment[ticker] = {"overall": round(random.uniform(2.0, 8.5), 2),
                             "confidence": round(random.uniform(1.0, 9.0), 2)}

records = {r["ticker"]: r for r in scored}

# ── clustered.json, via rmt_cluster's own resolve_cluster ────────────────
scores = {t: records[t]["composite"] for t in records}
raw_clusters = [
    {"members": ["BMO.TO", "CM.TO", "NA.TO", "BNS.TO", "RY.TO"],
     "avg_correlation": 0.74, "mode_rank": 1, "mode_side": "positive",
     "eigenvalue": 2.9103, "pct_variance": 22.39},
    {"members": ["MSFT", "NVDA", "AMD"],
     "avg_correlation": 0.61, "mode_rank": 2, "mode_side": "negative",
     "eigenvalue": 1.8842, "pct_variance": 14.49},
    {"members": ["CNQ.TO", "SU.TO"],
     "avg_correlation": 0.68, "mode_rank": 3, "mode_side": "positive",
     "eigenvalue": 1.2209, "pct_variance": 9.39},
]
clusters = [ps.rc.resolve_cluster(dict(c), scores) for c in raw_clusters]
clustered_members = {t for c in clusters for t in c["members"]}
standalone = sorted(set(records) - clustered_members)

clustered = {
    "generated_at": STAMP,
    "insufficient_data_for_clustering": False,
    "params": {"input": "scored_candidates.json", "shortlist_size": len(records),
               "top": None, "min_composite": None, "target_q": 5.0,
               "loading_k": 1.0, "min_cluster_correlation": 0.3,
               "dispersion_threshold": 1.0, "min_names_for_clustering": 5,
               "lookback_years": 3.0, "period": "3y",
               "assets_in_matrix": len(records), "trading_days": 754,
               "q_ratio": 58.0, "mp_threshold": 1.2714,
               "mp_threshold_sigma1": 1.3016, "noise_sigma2": 0.9768,
               "signals_found": 4, "signals_found_sigma1": 3,
               "effective_independent_bets": 2.71},
    "dropped_duplicates": [],
    "clusters": clusters,
    "standalone": standalone,
    "standalone_notes": {t: "no significant loading on any genuine correlation mode"
                         for t in standalone},
    "logic_version": getattr(ps.rc, "CLUSTER_VERSION", "1.0"),
}

cluster_view = {}
for index, cluster in enumerate(clusters):
    for ticker in cluster["members"]:
        cluster_view[ticker] = {
            "cluster_index": index, "members": cluster["members"],
            "avg_correlation": cluster["avg_correlation"],
            "dispersion": cluster.get("dispersion"),
            "resolution": cluster.get("resolution"),
            "winner": cluster.get("winner"),
            "is_winner": cluster.get("winner") == ticker,
            "demoted": ticker in (cluster.get("demoted_peers") or []),
        }

# ── correlations, with the awkward cases the real file can contain ───────
held_tickers = [h["ticker"] for h in HOLDINGS]
correlations = {}
for ticker in records:
    if ticker in held_tickers:
        continue
    pairs = {}
    for holding in held_tickers:
        same_sector = (records[ticker]["sector"]
                       == records.get(holding, {}).get("sector"))
        base = random.uniform(0.62, 0.93) if same_sector else random.uniform(-0.25, 0.55)
        pairs[holding] = {"raw": round(base, 4),
                          "cleaned": round(base - random.uniform(0.02, 0.08), 4)}
    # One name whose only stored pair is unusable - the case the brief's
    # naive max() crashed on.
    if ticker == "XYZ.V":
        pairs = {held_tickers[0]: {"raw": None, "cleaned": float("nan")}}
    correlations[ticker] = pairs

# ── sizing, through position_sizer itself ───────────────────────────────
by_ticker = {h["ticker"]: h for h in HOLDINGS}
out_candidates = []
for ticker in sorted(records, key=lambda t: -records[t]["composite"]):
    record = records[ticker]
    holding = by_ticker.get(ticker)
    guidance, sizing = ps.size_candidate(
        ticker, record, cluster_view.get(ticker), holding, correlations,
        threshold=ps.CORR_THRESHOLD, reduction_factor=ps.REDUCTION_FACTOR,
        flat=False, holdings_on_file=True, basis=ps.CORRELATION_BASIS,
        sentiment_scores=sentiment.get(ticker))
    out_candidates.append({
        "ticker": ticker, "name": record["name"], "sector": record["sector"],
        "quote_currency": record["quote_currency"],
        "composite": record["composite"], "rating": record["rating"],
        "dims": record["dims"], "already_held": holding is not None,
        "holding": holding, "cluster": cluster_view.get(ticker),
        "position_guidance": guidance, "sizing": sizing,
        "divergence_pattern": record["divergence_pattern"],
        "liquidity_flag": record["liquidity_flag"],
        "trend_years_available": record["trend_years_available"],
        "roa_trend_consistent": record["roa_trend_consistent"],
        "roa_trend": record["roa_trend"],
        "fcf_positive_years": record["fcf_positive_years"],
        "debt_trend": record["debt_trend"],
        "data_coverage": record["data_coverage"],
        "financials_as_of": record["financials_as_of"],
        "price_as_of": record["price_as_of"],
        "warnings": record["warnings"],
        "metrics": record["metrics"], "frameworks": record["frameworks"],
        "liquidity": record["liquidity"], "value_screen": None, "insider": None,
    })

holdings_review = []
for holding in HOLDINGS:
    ticker = holding["ticker"]
    record = records.get(ticker)
    composite = record["composite"] if record else None
    previous = (round(composite + random.uniform(-2.0, 0.6), 2)
                if composite is not None else None)
    delta = (round(composite - previous, 2)
             if composite is not None and previous is not None else None)
    if composite is None:
        verdict, reasons = "unavailable", ["could not be re-scored"]
    elif delta is not None and delta <= -ps.EXIT_DROP:
        verdict, reasons = "exit_review", [
            f"composite fell {abs(delta):.2f} since the last archived scan"]
    elif composite < ps.EXIT_WATCH_COMPOSITE:
        verdict, reasons = "watch", [
            f"composite {composite:.2f} under {ps.EXIT_WATCH_COMPOSITE:.1f}"]
    else:
        verdict, reasons = "hold", ["fundamentals still support the position"]
    holdings_review.append({
        "ticker": ticker, "shares": holding["shares"],
        "cost_basis": holding["cost_basis"],
        "entry_date": holding.get("entry_date"),
        "entry_price": holding.get("entry_price"),
        "composite": composite, "previous_composite": previous,
        "composite_delta": delta,
        "rating": record["rating"] if record else None,
        "verdict": verdict, "reasons": reasons,
        "divergence_pattern": record["divergence_pattern"] if record else None,
        "roa_trend_consistent": record["roa_trend_consistent"] if record else None,
        "roa_trend": record["roa_trend"] if record else None,
        "debt_trend": record["debt_trend"] if record else None,
        "data_coverage": record["data_coverage"] if record else None,
        "warnings": [],
    })

sized = {
    "generated_at": STAMP,
    "holdings_on_file": len(HOLDINGS),
    "note": None,
    "params": {
        "scored_input": str(OUT / "scored_candidates.json"),
        "clustered_input": str(OUT / "clustered.json"),
        "holdings_input": str(OUT / "holdings.json"),
        "holdings_template_created": False,
        "correlation_threshold": ps.CORR_THRESHOLD,
        "reduction_factor": ps.REDUCTION_FACTOR,
        "reduction_mode": "proportional",
        "correlation_basis": ps.CORRELATION_BASIS,
        "top": None, "min_composite": None, "slim": False,
        "correlation_panel": {
            "assets_requested": len(records) + len(HOLDINGS),
            "excluded": {"XYZ.V": "insufficient price history "
                                  "(412 of 754 rows, under 90% coverage)"},
            "lookback_years": 3.0, "period": "3y",
            "assets_in_matrix": len(records), "trading_days": 754,
            "q_ratio": 58.0, "mp_threshold": 1.2714, "noise_sigma2": 0.9768,
            "signals_found": 4,
            "holdings_in_matrix": held_tickers, "holdings_missing": [],
        },
        "conviction_sizing": True,
        "conviction_scale": ps.CONVICTION_SCALE,
        "sentiment_input": str(OUT / "sentiment.json"),
        "sentiment_names": len(sentiment),
    },
    "holdings": HOLDINGS,
    "holdings_review": holdings_review,
    "candidates": out_candidates,
    "logic_version": ps.SIZER_VERSION,
}

run_status = {
    "generated_at": STAMP, "completed": True, "ok": True,
    "stages": [
        {"stage": "universe",  "action": "skipped (fresh)", "age": 7200, "ok": True},
        {"stage": "evaluate",  "action": "ran (stale)",     "age": 120,  "ok": True},
        {"stage": "sentiment", "action": "ran (stale)",     "age": 110,  "ok": True,
         "required": False},
        {"stage": "cluster",   "action": "ran (stale)",     "age": 90,   "ok": True},
        {"stage": "size",      "action": "ran (stale)",     "age": 60,   "ok": True},
        {"stage": "timing",    "action": "ran (stale)",     "age": 40,   "ok": True,
         "required": False},
        {"stage": "exits",     "action": "ran (stale)",     "age": 20,   "ok": True,
         "required": False},
    ],
    "failed_stages": [], "blocking_stages": [],
    "invocation": {"force": False, "render_only": False,
                   "include_canada": True, "top": None,
                   "evaluate_limit": None, "sentiment_top": 15},
}

def write(name, document):
    pipe.common.write_json(document, OUT / name, default=pipe.common.json_default)

write("scored_candidates.json", {
    "generated_at": STAMP,
    "params": {"evaluator_version": se.EVALUATOR_VERSION,
               "min_coverage": se.MIN_COVERAGE, "low_coverage": se.LOW_COVERAGE},
    "counts": {"candidates": len(scored), "scored": len(scored), "skipped": 0},
    "scored": scored, "skipped": [],
    "logic_version": se.EVALUATOR_VERSION})
write("clustered.json", clustered)
write("sized_candidates.json", sized)
write("holdings.json", {
    "_comment": "Hand-edited holdings file.",
    "holdings": HOLDINGS + [{"ticker": "AAPL", "shares": 10,
                             "cost_basis": 150.0, "_example": True}]})
write("sentiment.json", {"generated_at": STAMP, "sentiment": sentiment,
                         "logic_version": "1.0"})
write("run_status.json", run_status)

print(f"wrote fixtures to {OUT}")
print(f"  {len(out_candidates)} candidates, {len(HOLDINGS)} holdings, "
      f"{len(clusters)} clusters, {len(standalone)} standalone")
cut = [c for c in out_candidates if c['sizing']['correlation_adjusted']]
conv = [c for c in out_candidates if c['sizing']['conviction_scale'] != 1.0]
print(f"  {len(cut)} correlation-cut, {len(conv)} with a conviction modifier")
