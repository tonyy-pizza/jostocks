r"""jos/corr.py - the full correlation matrix the heatmap needs.

The matrix is not saved anywhere. sized_candidates.json keeps each candidate's
correlation to each HOLDING (sizing.correlations) and clustered.json keeps
per-cluster averages, so neither has candidate-to-candidate values. The
heatmap wants the whole block, so it is computed here - in a worker thread,
from cached prices, with no network in the normal case.

Faithfulness is the whole problem. A correlation estimated on one panel is not
comparable with one estimated on another: the MP threshold depends on the
shape of the panel it was fitted to, so a matrix built over a different
universe or a different lookback would show numbers that quietly disagree with
the sizing the app is displaying beside them. So this does exactly what
position_sizer.correlation_to_holdings does:

    universe = sorted(set(candidates) | set(holdings))      # one panel, together
    years, period = params.correlation_panel, else adaptive_lookback(len(universe))
    prices, excluded = rc.build_price_frame(universe, period)
    decomp = rc.rmt_decompose(prices)
    cleaned, raw = rc.clean_correlation(decomp), decomp["corr"]

and then checks itself: every candidate/holding pair it computed must match
the value already in sizing.correlations to within 0.01, on both bases. A
mismatch is surfaced on the panel rather than silently drawn, because numbers
that disagree with the sizing are worse than a panel that says it cannot be
trusted.

The result is cached in data/corr_matrix.json keyed on the ticker set plus the
scan's generated_at, so it recomputes after a new scan and not before.
"""

from __future__ import annotations

import hashlib

from . import pipeline as pipe
from .paths import CORR_CACHE_PATH
from .state import read_json, write_json

#: Section 6: the recomputed value must agree with the stored one this closely.
SELF_CHECK_TOLERANCE = 0.01

CACHE_VERSION = 2


def cache_key(tickers, generated_at) -> str:
    """Identity of a matrix: which names, and which scan.

    The ticker set is hashed rather than stored so the key stays short, and
    sorted first so the same set in a different order is the same key.
    """
    names = ",".join(sorted(set(t for t in tickers if t)))
    digest = hashlib.md5(names.encode("utf-8")).hexdigest()[:12]
    return f"v{CACHE_VERSION}:{digest}:{generated_at or 'unknown'}"


def load_cached(key, path=None):
    """A cached matrix for this key, or None."""
    document = read_json(path or CORR_CACHE_PATH)
    if not isinstance(document, dict):
        return None
    return document if document.get("key") == key else None


def save_cached(document, path=None) -> bool:
    return write_json(document, path or CORR_CACHE_PATH)


def panel_period(panel, universe_size):
    """(years, period) for the matrix, preferring what the scan recorded.

    Section 6 requires the same lookback and period the sizing panel used.
    adaptive_lookback is the fallback for a scan that recorded none, and it is
    the same function position_sizer would have called.
    """
    years = pipe.num((panel or {}).get("lookback_years"))
    period = (panel or {}).get("period")
    if years is not None and period:
        return float(years), str(period)
    if pipe.ready() and pipe.rc is not None:
        return pipe.rc.adaptive_lookback(max(1, universe_size))
    return 3.0, "3y"


def compute(candidates, holdings, panel=None, generated_at=None,
            stored_correlations=None, force_refresh=False, progress=None):
    """Build the matrix. Returns a plain dict, JSON-safe, for the cache.

        {key, generated_at, tickers, cleaned, raw, meta, excluded,
         mismatches, max_mismatch}

    `cleaned` and `raw` are {ticker: {ticker: value}} rather than a nested
    list, so a reordering in the view cannot silently transpose the matrix.
    """
    pipe.require()
    rc = pipe.rc

    universe = sorted(set(t for t in list(candidates or ()) + list(holdings or ()) if t))
    key = cache_key(universe, generated_at)

    if len(universe) < 3:
        return {"key": key, "generated_at": generated_at, "tickers": universe,
                "cleaned": {}, "raw": {}, "excluded": {},
                "meta": {"note": "too few names to estimate a correlation matrix"},
                "mismatches": [], "max_mismatch": None}

    years, period = panel_period(panel, len(universe))

    if progress:
        progress(f"computing correlations: {len(universe)} names over {period}...")

    prices, excluded = rc.build_price_frame(universe, period,
                                            force_refresh=force_refresh)
    if prices is None or len(prices.columns) < 3:
        return {"key": key, "generated_at": generated_at, "tickers": universe,
                "cleaned": {}, "raw": {}, "excluded": excluded or {},
                "meta": {"note": "not enough shared price history "
                                 "to estimate correlations"},
                "mismatches": [], "max_mismatch": None}

    shared_rows = len(prices) - 1
    if shared_rows < getattr(rc, "MIN_SHARED_ROWS", 60):
        return {"key": key, "generated_at": generated_at, "tickers": universe,
                "cleaned": {}, "raw": {}, "excluded": excluded or {},
                "meta": {"note": f"shared price window is only {shared_rows} "
                                 f"trading days; correlations would not be "
                                 f"meaningful"},
                "mismatches": [], "max_mismatch": None}

    if progress:
        progress("filtering noise (Marchenko-Pastur)...")

    decomp = rc.rmt_decompose(prices)
    cleaned_frame = rc.clean_correlation(decomp)
    raw_frame = decomp["corr"]

    meta = {
        "lookback_years": round(float(years), 2),
        "period": period,
        "assets_in_matrix": decomp["N"],
        "trading_days": decomp["T"],
        "q_ratio": round(decomp["q"], 2),
        "mp_threshold": round(decomp["threshold"], 4),
        "noise_sigma2": round(decomp["sigma2"], 4),
        "signals_found": decomp["n_signal"],
    }
    min_q = getattr(rc, "MIN_USABLE_Q", 1.5)
    if decomp["q"] < min_q:
        meta["warning"] = (f"T/N = {decomp['q']:.2f} is below {min_q}; "
                           f"correlations are unreliable.")

    order = [t for t in universe if t in cleaned_frame.columns]
    cleaned = {a: {b: _round(cleaned_frame.loc[a, b]) for b in order} for a in order}
    raw = {a: {b: _round(raw_frame.loc[a, b]) for b in order} for a in order}

    mismatches, worst = self_check(cleaned, raw, stored_correlations)

    return {"key": key, "generated_at": generated_at, "tickers": order,
            "cleaned": cleaned, "raw": raw, "excluded": excluded or {},
            "meta": meta, "mismatches": mismatches, "max_mismatch": worst}


def _round(value):
    number = pipe.num(value)
    return None if number is None else round(float(number), 4)


def self_check(cleaned, raw, stored_correlations,
               tolerance=SELF_CHECK_TOLERANCE):
    """Compare the recomputed pairs against the ones the sizing used.

    Returns (mismatches, worst_absolute_delta). Every candidate/holding pair
    the file recorded is checked on both bases; a pair the matrix excluded is
    not a mismatch, it is a name that did not make the panel, which the footer
    reports separately.
    """
    if not stored_correlations:
        return [], None

    problems, worst = [], None
    for candidate, pairs in (stored_correlations or {}).items():
        if candidate not in cleaned:
            continue
        for holding, values in (pairs or {}).items():
            if holding not in (cleaned.get(candidate) or {}):
                continue
            for basis, table in (("cleaned", cleaned), ("raw", raw)):
                stored = pipe.num((values or {}).get(basis))
                fresh = pipe.num(table[candidate].get(holding))
                if stored is None or fresh is None:
                    continue
                delta = abs(fresh - stored)
                if worst is None or delta > worst:
                    worst = delta
                if delta > tolerance:
                    problems.append({
                        "pair": f"{candidate}/{holding}",
                        "basis": basis,
                        "stored": round(stored, 4),
                        "recomputed": round(fresh, 4),
                        "delta": round(fresh - stored, 4),
                    })

    problems.sort(key=lambda p: -abs(p["delta"]))
    return problems, (None if worst is None else round(worst, 4))


def matrix_for(scan, force_refresh=False, progress=None):
    """The matrix for a Scan, from cache when it is still valid.

    Called from a worker. The cache key carries the scan's generated_at, so a
    new scan invalidates it and nothing else does.
    """
    candidates = list(scan.by_ticker)
    holdings = scan.holding_tickers
    key = cache_key(candidates + holdings, scan.generated_at)

    if not force_refresh:
        cached = load_cached(key)
        if cached is not None:
            return cached

    stored = {ticker: (candidate.get("sizing") or {}).get("correlations") or {}
              for ticker, candidate in scan.by_ticker.items()}

    document = compute(candidates, holdings,
                       panel=scan.correlation_panel,
                       generated_at=scan.generated_at,
                       stored_correlations=stored,
                       force_refresh=force_refresh,
                       progress=progress)
    save_cached(document)
    return document


def ordered_tickers(scan, matrix) -> list:
    """Row/column order for the heatmap: clusters, standalone, holdings last.

    Section 6: members of one cluster adjacent so a cluster reads as a block
    on the diagonal, then standalone names, then the holdings with a divider
    before them.
    """
    present = set((matrix or {}).get("tickers") or ())
    order = [t for t in scan.cluster_order() if t in present]
    seen = set(order)
    for ticker in scan.holding_tickers:
        if ticker in present and ticker not in seen:
            order.append(ticker)
            seen.add(ticker)
    for ticker in (matrix or {}).get("tickers") or ():
        if ticker not in seen:
            order.append(ticker)
            seen.add(ticker)
    return order


def cluster_blocks(scan, order) -> list:
    """Where the cluster frames go, as index ranges into `order`.

    Returns [{start, end, resolution, winner, label}]. Only contiguous runs
    are framed: if the ordering could not keep a cluster together, drawing a
    frame around the gap would claim a block that is not there.
    """
    position = {ticker: i for i, ticker in enumerate(order)}
    blocks = []
    for index, cluster in enumerate(scan.clusters):
        members = [t for t in (cluster.get("members") or []) if t in position]
        if len(members) < 2:
            continue
        indices = sorted(position[t] for t in members)
        if indices[-1] - indices[0] != len(indices) - 1:
            continue
        blocks.append({
            "start": indices[0],
            "end": indices[-1],
            "index": index,
            "resolution": cluster.get("resolution"),
            "winner": cluster.get("winner"),
            "label": cluster.get("resolution") or "",
        })
    return blocks
