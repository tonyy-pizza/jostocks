r"""jos/sectors.py - the scored candidates, grouped by sector.

One row per sector: how many names, the average on the chosen basis, and the
best one. Clicking a row filters the candidates table. This panel needs no new
data at all - it is an aggregate of what the scan already wrote.

THE BASIS TOGGLE, AND WHY IT IS BUILT FROM THE DATA
---------------------------------------------------
The brief asks for "composite / valuation / quality" and adds: use the
matching `dims` or metrics, check what keys actually exist, and only offer
what the data supports. Checking is the operative word - `build_scores()`
writes exactly six dimensions:

    Valuation, Profitability, Growth, Health, Momentum, Frameworks

There is no "quality". The nearest things to it are Profitability and Health,
which are separate dimensions measuring different claims, and collapsing them
into one invented number would be this app asserting something the evaluator
does not. So the toggle is composite plus whichever of those six are actually
present in the loaded data, discovered per scan rather than hardcoded. A
scoring change that adds or renames a dimension shows up here on its own.

Averages skip names with nothing to average rather than scoring them zero: a
sector whose names all had their Valuation suppressed has no Valuation
average, and saying so is better than reporting 0.0 and implying the sector is
cheap in the worst possible way.
"""

from __future__ import annotations

from . import pipeline as pipe

#: The basis every scan supports, whatever its dims.
COMPOSITE = "composite"


def available_bases(candidates) -> list:
    """[(key, label)] for the basis toggle, from the data in hand.

    Composite first, then the dimensions the evaluator actually wrote, in its
    own order. A dimension that is present but null for every name is left
    out - offering a basis that can only produce "n/a" is not an option.
    """
    bases = [(COMPOSITE, "composite")]

    seen = {}
    for candidate in candidates or ():
        dims = candidate.get("dims")
        if not isinstance(dims, dict):
            continue
        for key, value in dims.items():
            if pipe.num(value) is not None:
                seen[key] = True

    for key in pipe.dims_order(seen):
        bases.append((key, key.lower()))
    return bases


def value_for(candidate, basis):
    """One candidate's score on the chosen basis, or None."""
    if basis == COMPOSITE:
        return pipe.num(candidate.get("composite"))
    dims = candidate.get("dims")
    if not isinstance(dims, dict):
        return None
    return pipe.num(dims.get(basis))


def summarise(candidates, basis=COMPOSITE) -> list:
    """One row per sector, best average first.

    Returns [{sector, count, average, best, best_value, held, measured,
              unmeasured}], where `measured` is how many names in the sector
    had a value on this basis - the honest denominator for the average.
    """
    groups = {}
    for candidate in candidates or ():
        sector = candidate.get("sector") or "unknown sector"
        entry = groups.setdefault(sector, {
            "sector": sector, "count": 0, "values": [], "best": None,
            "best_value": None, "held": 0,
        })
        entry["count"] += 1
        if candidate.get("already_held"):
            entry["held"] += 1

        value = value_for(candidate, basis)
        if value is None:
            continue
        entry["values"].append(value)
        if entry["best_value"] is None or value > entry["best_value"]:
            entry["best_value"] = value
            entry["best"] = candidate.get("ticker")

    rows = []
    for entry in groups.values():
        values = entry.pop("values")
        entry["measured"] = len(values)
        entry["unmeasured"] = entry["count"] - len(values)
        entry["average"] = (round(sum(values) / len(values), 2) if values
                            else None)
        rows.append(entry)

    # Best average first; a sector with nothing measured sinks to the bottom
    # rather than sorting as if it scored zero.
    rows.sort(key=lambda r: (r["average"] is None, -(r["average"] or 0),
                             r["sector"]))
    return rows
