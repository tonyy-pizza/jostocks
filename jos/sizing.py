r"""jos/sizing.py - recompute adjusted sizing as the sliders move.

The sliders change four things: the correlation threshold, the reduction
factor, which correlation basis the threshold is applied to, and whether the
reduction is proportional or flat. None of them needs the network or a
pipeline run, because sized_candidates.json already stores every candidate's
raw and cleaned correlation to every holding.

WHY THIS IS NOT THE THREE LINES THE BRIEF SUGGESTS
--------------------------------------------------
Section 4 offers this sketch:

    worst = max(pairs, key=lambda h: pairs[h][basis])
    scale, reduction = sizing_scale(pairs[worst][basis], threshold, factor, flat=flat)
    adjusted = apply_reduction(candidate["sizing"]["base_guide"], scale)

Two things in it disagree with the pipeline, and both are documented in
position_sizer.py itself:

1. That `max` is the exact bug worst_correlation() was written to fix. A
   stored None makes `.get(basis, 0.0)`'s default never fire (the key is
   there, its value is None), so max() returns that holding and the caller
   compares None <= threshold, a TypeError that took the whole sizing stage
   down. A stored NaN compares False against everything, so it slipped past
   the threshold test and drove a full-size cut on a number nobody computed.
   Both mean "we do not know", and not knowing is not evidence a candidate
   duplicates a holding. So worst_correlation() is imported, not rewritten.

2. The correlation scale is not the whole scale. size_candidate() multiplies
   it by a CONVICTION scale - the sentiment/divergence verdict, 1.00 down to
   0.50 - and writes the product. Applying only the correlation part would
   make the app's adjusted guide disagree with the pipeline's by up to half
   on any name with a contested read. The conviction scale is stored per
   candidate, so it is read back and multiplied in, exactly as
   size_candidate's finalize() does.

Everything below mirrors size_candidate()'s branch order so the two produce
the same answer for the same inputs. verify() checks that against the file.
"""

from __future__ import annotations

from . import pipeline as pipe

#: Rounding the pipeline uses, so a value-for-value comparison matches.
SCALE_DIGITS = 4
PCT_DIGITS = 2


def defaults():
    """The pipeline's own constants, for a file that recorded no params."""
    if pipe.ready() and pipe.ps is not None:
        return {
            "threshold": float(getattr(pipe.ps, "CORR_THRESHOLD", 0.70)),
            "reduction_factor": float(getattr(pipe.ps, "REDUCTION_FACTOR", 0.50)),
            "basis": getattr(pipe.ps, "CORRELATION_BASIS", "raw"),
            "flat": False,
        }
    return {"threshold": 0.70, "reduction_factor": 0.50, "basis": "raw",
            "flat": False}


def _worst(pairs, basis):
    """(holding, correlation) on the chosen basis, via the pipeline's helper."""
    if pipe.ready() and pipe.ps is not None:
        return pipe.ps.worst_correlation(pairs, basis)

    # Same contract as worst_correlation, for a UI running without the
    # pipeline: skip anything that is not a finite number, largest signed
    # value wins (a strongly negative correlation is not a duplicate).
    usable = {}
    for holding, values in (pairs or {}).items():
        if not isinstance(values, dict):
            continue
        number = pipe.num(values.get(basis))
        if number is not None:
            usable[holding] = number
    if not usable:
        return None, None
    holding = max(usable, key=lambda h: usable[h])
    return holding, usable[holding]


def _scale(correlation, threshold, factor, flat):
    """(scale, reduction) via position_sizer.sizing_scale."""
    if pipe.ready() and pipe.ps is not None:
        return pipe.ps.sizing_scale(correlation, threshold, factor, flat=flat)

    if correlation is None or correlation <= threshold:
        return 1.0, 0.0
    if flat:
        reduction = factor
    else:
        span = max(1e-9, 1.0 - threshold)
        reduction = factor * (correlation - threshold) / span
    reduction = max(0.0, min(factor, reduction))
    return 1.0 - reduction, reduction


def _apply(guide, scale):
    """Rewrite a guide string with every percentage scaled.

    Every percentage, not only the range: "Core: 3%-5%; up to 8% with
    diversification." carries a range AND a cap, and scaling only the first
    leaves one sentence quoting an 8% ceiling on a position just cut to 2.5%.
    """
    if pipe.ready() and pipe.ps is not None:
        return pipe.ps.apply_reduction(guide, scale)
    return guide


def _range(guide):
    """(low, high) percentages from a guide string."""
    if pipe.ready() and pipe.ps is not None:
        return pipe.ps.parse_guide_range(guide)
    return None, None


def recompute(candidate, threshold, reduction_factor, basis="raw", flat=False):
    """What this candidate's sizing becomes under these controls.

    Returns a dict shaped like the stored `sizing` block's adjusted half, so a
    view can read either without caring which it has:

        correlation_adjusted, correlation_scale, scale, reduction,
        adjusted_guide, adjusted_low_pct, adjusted_high_pct,
        max_correlation, max_correlation_raw, max_correlation_cleaned,
        correlated_with, correlation_basis, conviction_scale, note

    The branch order is size_candidate()'s: held first, then no holdings on
    file, then no pairs, then no usable pair, then below threshold, then the
    actual reduction. Each of those returns the base guide scaled by the
    conviction modifier alone - which is what the pipeline writes too, and the
    reason a held name still shows a reduction when its read is contested.
    """
    stored = dict(candidate.get("sizing") or {})
    base_guide = stored.get("base_guide")

    base_low = pipe.num(stored.get("base_low_pct"))
    base_high = pipe.num(stored.get("base_high_pct"))
    if base_low is None and base_high is None and base_guide:
        base_low, base_high = _range(base_guide)

    conviction_scale = pipe.num(stored.get("conviction_scale"))
    if conviction_scale is None:
        conviction_scale = 1.0

    out = {
        "base_guide": base_guide,
        "base_low_pct": base_low,
        "base_high_pct": base_high,
        "conviction": stored.get("conviction"),
        "conviction_scale": conviction_scale,
        "correlation_basis": basis,
        "correlation_adjusted": False,
        "correlation_scale": 1.0,
        "max_correlation": None,
        "max_correlation_raw": None,
        "max_correlation_cleaned": None,
        "correlated_with": None,
        "note": None,
    }

    def finalize(correlation_scale=1.0):
        total = correlation_scale * conviction_scale
        out["correlation_scale"] = round(correlation_scale, SCALE_DIGITS)
        out["scale"] = round(total, SCALE_DIGITS)
        out["reduction"] = round(1.0 - total, SCALE_DIGITS)
        out["adjusted_guide"] = (_apply(base_guide, total) if total != 1.0
                                 else base_guide)
        out["adjusted_low_pct"] = (None if base_low is None
                                   else round(base_low * total, PCT_DIGITS))
        out["adjusted_high_pct"] = (None if base_high is None
                                    else round(base_high * total, PCT_DIGITS))
        return out

    # Held names are shown but never correlation-cut: owning more of a
    # position you already hold is the thing the modifier exists to describe,
    # not something to describe about itself.
    if candidate.get("already_held"):
        out["note"] = "already held - not sized as a new position"
        return finalize()

    pairs = stored.get("correlations") or {}
    if not pairs:
        out["note"] = (stored.get("note")
                       or "no correlation to holdings available for this name")
        return finalize()

    worst, worst_corr = _worst(pairs, basis)
    if worst is None:
        out["note"] = (f"no usable {basis} correlation stored for this name "
                       f"({len(pairs)} holding(s) compared, all missing or NaN)")
        return finalize()

    out["max_correlation"] = worst_corr
    out["max_correlation_raw"] = pipe.num(pairs[worst].get("raw"))
    out["max_correlation_cleaned"] = pipe.num(pairs[worst].get("cleaned"))

    if worst_corr <= threshold:
        out["note"] = (f"no meaningful correlation to holdings "
                       f"(max {worst_corr:.2f} {basis} with {worst}, "
                       f"at or below {threshold:.2f})")
        return finalize()

    scale, reduction = _scale(worst_corr, threshold, reduction_factor, flat)
    out.update({
        "correlation_adjusted": True,
        "correlated_with": worst,
        "note": (f"correlation {worst_corr:.2f} ({basis}) with holding {worst} "
                 f"above {threshold:.2f}: size cut {reduction * 100:.0f}%"),
    })
    return finalize(scale)


def recompute_all(candidates, threshold, reduction_factor, basis="raw",
                  flat=False) -> dict:
    """{ticker: recompute(...)} for every candidate."""
    out = {}
    for candidate in candidates or ():
        ticker = candidate.get("ticker")
        if ticker:
            out[ticker] = recompute(candidate, threshold, reduction_factor,
                                    basis=basis, flat=flat)
    return out


def guide_short(guide) -> str:
    """The first clause of a guide string.

    scan_report prints `adjusted_guide.split(";")[0]`, so a table column that
    has to fit shows the same words the report does.
    """
    return str(guide or "").split(";")[0].strip()


def cut_label(sizing) -> str:
    """"cut 23%" when a correlation cut applies, else "".

    Only when correlation_adjusted, matching scan_report: a name reduced by
    conviction alone has a reduction but no correlation cut, and calling that
    a cut would attribute it to a holding that is not responsible.
    """
    if not (sizing or {}).get("correlation_adjusted"):
        return ""
    reduction = pipe.num(sizing.get("reduction"))
    if reduction is None or reduction <= 0:
        return ""
    return f"cut {reduction * 100:.0f}%"


def verify(candidates, params, tolerance=0.005) -> list:
    """Re-derive the stored sizing with the stored controls and compare.

    A guard against this module drifting from position_sizer: run the scan's
    own parameters through recompute() and every candidate should come back
    with the scale already on disk. A mismatch means the pipeline's sizing
    changed and this needs to follow, which is worth saying out loud rather
    than quietly showing different numbers from the terminal report.

    Returns a list of {ticker, stored, recomputed, delta}, empty when all
    agree.
    """
    threshold = pipe.num((params or {}).get("correlation_threshold"))
    factor = pipe.num((params or {}).get("reduction_factor"))
    basis = (params or {}).get("correlation_basis") or "raw"
    flat = (params or {}).get("reduction_mode") == "flat"

    fallback = defaults()
    if threshold is None:
        threshold = fallback["threshold"]
    if factor is None:
        factor = fallback["reduction_factor"]

    problems = []
    for candidate in candidates or ():
        stored = pipe.num((candidate.get("sizing") or {}).get("scale"))
        if stored is None:
            continue
        fresh = pipe.num(recompute(candidate, threshold, factor,
                                   basis=basis, flat=flat).get("scale"))
        if fresh is None:
            continue
        if abs(fresh - stored) > tolerance:
            problems.append({"ticker": candidate.get("ticker"),
                             "stored": stored, "recomputed": fresh,
                             "delta": round(fresh - stored, 4)})
    return problems
