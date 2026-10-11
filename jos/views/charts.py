r"""jos/views/charts.py - the figures, as plain dicts.

Every function here returns a JSON-safe {"data": [...], "layout": {...}} and
touches no Qt and no web view, which means each one can be checked in a test
without a display. That matters more than it sounds: these are the parts of
the app that are easiest to get subtly wrong (a transposed matrix, a
mislabelled axis) and hardest to see wrong in a screenshot.

Shared layout decisions, from section 5: panel-coloured paper, cream text,
hard corners (no rounded plot frame), the single-hue ramp for the heatmap, and
amber for anything that marks the current threshold.
"""

from __future__ import annotations

from .. import pipeline as pipe
from .. import theme
from ..datastore import is_canadian, venue

#: Plotly wants an explicit colourscale for a single hue. Section 5 specifies
#: rgba(111,168,220,a), so the ramp is that colour from transparent to full.
HEATMAP_SCALE = [
    [0.0, "rgba(111,168,220,0.03)"],
    [0.25, "rgba(111,168,220,0.25)"],
    [0.5, "rgba(111,168,220,0.48)"],
    [0.75, "rgba(111,168,220,0.72)"],
    [1.0, "rgba(111,168,220,1.0)"],
]


def _base_layout(**overrides) -> dict:
    """The layout every figure starts from."""
    families = theme.font_families()
    layout = {
        "paper_bgcolor": theme.PANEL_BG,
        "plot_bgcolor": theme.PANEL_BG,
        "font": {"color": theme.CREAM, "family": families["body"], "size": 11},
        "margin": {"l": 56, "r": 16, "t": 16, "b": 44},
        "hoverlabel": {
            "bgcolor": theme.BG,
            "bordercolor": theme.CONTROL_BORDER,
            "font": {"color": theme.CREAM, "family": families["mono"], "size": 11},
        },
        "showlegend": False,
        "dragmode": False,
    }
    layout.update(overrides)
    return layout


def _axis(**overrides) -> dict:
    axis = {
        "gridcolor": theme.PANEL_BORDER,
        "zerolinecolor": theme.PANEL_BORDER,
        "linecolor": theme.PANEL_BORDER,
        "tickfont": {"color": theme.MUTED, "size": 10},
        "title": {"font": {"color": theme.MUTED, "size": 11}},
        "showgrid": True,
        "zeroline": False,
    }
    axis.update(overrides)
    return axis


def empty(message) -> dict:
    """A figure that just says why there is nothing to draw."""
    return {
        "data": [],
        "layout": _base_layout(
            xaxis={"visible": False}, yaxis={"visible": False},
            annotations=[{
                "text": str(message),
                "showarrow": False,
                "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5,
                "font": {"color": theme.MUTED, "size": 12},
                "align": "center",
            }]),
    }


# ─── correlation heatmap ─────────────────────────────────────────────────

def heatmap(matrix, order, holdings, threshold, basis="cleaned", blocks=None,
            scan=None) -> dict:
    """The candidate/holding correlation block.

    Section 6, precisely:
      - cell value is the MP-cleaned correlation on a 0-1 scale; negatives
        clamp to the low end of the ramp but keep their true number in the
        tooltip, because a negative correlation is a real reading and not a
        duplicate position,
      - cells at or above the threshold get a thin amber outline that moves
        with the slider,
      - cluster blocks get a one-pixel cream frame labelled with the
        resolution,
      - holdings sit last, after a divider, and their labels carry "held".
    """
    cleaned = (matrix or {}).get("cleaned") or {}
    raw = (matrix or {}).get("raw") or {}
    order = [t for t in (order or ()) if t in cleaned]

    if len(order) < 2:
        note = ((matrix or {}).get("meta") or {}).get("note")
        return empty(note or "not enough names with shared price history "
                             "to build a correlation matrix")

    held = set(holdings or ())
    primary = cleaned if basis == "cleaned" else raw
    other = raw if basis == "cleaned" else cleaned

    values, hover = [], []
    for row_ticker in order:
        row_values, row_hover = [], []
        for column_ticker in order:
            shown = pipe.num((primary.get(row_ticker) or {}).get(column_ticker))
            alternate = pipe.num((other.get(row_ticker) or {}).get(column_ticker))

            # Clamp only what is DRAWN. The tooltip keeps the true number.
            row_values.append(None if shown is None else max(0.0, min(1.0, shown)))

            if row_ticker == column_ticker:
                row_hover.append(f"{row_ticker} with itself")
            else:
                cleaned_value = shown if basis == "cleaned" else alternate
                raw_value = alternate if basis == "cleaned" else shown
                row_hover.append(
                    f"{row_ticker} / {column_ticker}<br>"
                    f"cleaned {_fmt(cleaned_value)} &nbsp; raw {_fmt(raw_value)}"
                    + ("<br>at or above the threshold"
                       if shown is not None and shown >= threshold else ""))
        values.append(row_values)
        hover.append(row_hover)

    labels = [f"{t} · held" if t in held else t for t in order]

    trace = {
        "type": "heatmap",
        "z": values,
        "x": labels,
        "y": labels,
        "colorscale": HEATMAP_SCALE,
        "zmin": 0.0,
        "zmax": 1.0,
        "showscale": False,
        "xgap": 1,
        "ygap": 1,
        "text": hover,
        "hovertemplate": "%{text}<extra></extra>",
    }

    shapes = []

    # Cells at or above the threshold: a thin amber outline that follows the
    # slider. Drawn as shapes rather than a second trace so the outline sits
    # on the cell rather than replacing its fill.
    for row_index, row_ticker in enumerate(order):
        for column_index, column_ticker in enumerate(order):
            if row_ticker == column_ticker:
                continue
            value = pipe.num((primary.get(row_ticker) or {}).get(column_ticker))
            if value is None or value < threshold:
                continue
            shapes.append({
                "type": "rect", "xref": "x", "yref": "y",
                "x0": column_index - 0.5, "x1": column_index + 0.5,
                "y0": row_index - 0.5, "y1": row_index + 0.5,
                "line": {"color": theme.AMBER, "width": 1},
                "fillcolor": "rgba(0,0,0,0)", "layer": "above",
            })

    annotations = []

    # Cluster frames, in cream, labelled with the resolution.
    for block in (blocks or ()):
        start, end = block["start"], block["end"]
        shapes.append({
            "type": "rect", "xref": "x", "yref": "y",
            "x0": start - 0.5, "x1": end + 0.5,
            "y0": start - 0.5, "y1": end + 0.5,
            "line": {"color": theme.CREAM, "width": 1},
            "fillcolor": "rgba(0,0,0,0)", "layer": "above",
        })
        label = block.get("label") or ""
        if label:
            annotations.append({
                "x": (start + end) / 2.0, "y": start - 0.62,
                "text": label,
                "showarrow": False,
                "font": {"color": theme.CYAN if label == "industry_wide"
                                  else theme.CREAM, "size": 9},
                "yanchor": "bottom",
            })

    # The divider before the holdings, so "held last" is visible rather than
    # implied by the labels.
    first_held = next((i for i, t in enumerate(order) if t in held), None)
    if first_held not in (None, 0):
        shapes.append({
            "type": "line", "xref": "x", "yref": "paper",
            "x0": first_held - 0.5, "x1": first_held - 0.5, "y0": 0, "y1": 1,
            "line": {"color": theme.CONTROL_BORDER, "width": 1, "dash": "dot"},
            "layer": "above",
        })
        shapes.append({
            "type": "line", "xref": "paper", "yref": "y",
            "x0": 0, "x1": 1, "y0": first_held - 0.5, "y1": first_held - 0.5,
            "line": {"color": theme.CONTROL_BORDER, "width": 1, "dash": "dot"},
            "layer": "above",
        })

    size = len(order)
    layout = _base_layout(
        margin={"l": 78, "r": 12, "t": 26, "b": 78},
        xaxis=_axis(showgrid=False, tickangle=-60, side="bottom",
                    constrain="domain",
                    tickfont={"color": theme.MUTED,
                              "size": 9 if size > 24 else 10}),
        yaxis=_axis(showgrid=False, autorange="reversed",
                    scaleanchor="x", constrain="domain",
                    tickfont={"color": theme.MUTED,
                              "size": 9 if size > 24 else 10}),
        shapes=shapes,
        annotations=annotations,
    )
    return {"data": [trace], "layout": layout}


def _fmt(value) -> str:
    number = pipe.num(value)
    return "n/a" if number is None else f"{number:+.2f}"


# ─── score vs correlation ────────────────────────────────────────────────

def score_vs_correlation(candidates, sizing_by_ticker, threshold,
                         basis="raw") -> dict:
    """x = max correlation to holdings, y = composite (section 6).

    Points are split into two traces, reduced and full size, because that is
    the distinction the panel exists to show - and two traces means the
    difference is carried by position in the legend as well as by colour.
    A vertical dashed amber line marks the threshold and moves with it.
    """
    reduced = {"x": [], "y": [], "text": []}
    full = {"x": [], "y": [], "text": []}
    held = {"x": [], "y": [], "text": []}

    for candidate in candidates or ():
        ticker = candidate.get("ticker")
        composite = pipe.num(candidate.get("composite"))
        live = (sizing_by_ticker or {}).get(ticker) or candidate.get("sizing") or {}
        correlation = pipe.num(live.get("max_correlation"))
        if composite is None or correlation is None:
            continue

        label = (f"{ticker}<br>composite {composite:.2f}"
                 f"<br>max {basis} correlation {correlation:+.2f}")
        if live.get("correlated_with"):
            label += f" with {live['correlated_with']}"
        cut = pipe.num(live.get("reduction"))
        if cut:
            label += f"<br>size cut {cut * 100:.0f}%"

        if candidate.get("already_held"):
            bucket = held
        elif live.get("correlation_adjusted"):
            bucket = reduced
        else:
            bucket = full
        bucket["x"].append(correlation)
        bucket["y"].append(composite)
        bucket["text"].append(label)

    if not (reduced["x"] or full["x"] or held["x"]):
        return empty("no candidate has a correlation to a holding yet - "
                     "add positions to holdings.json and re-run the scan")

    def _trace(bucket, name, colour, symbol):
        return {
            "type": "scatter", "mode": "markers", "name": name,
            "x": bucket["x"], "y": bucket["y"], "text": bucket["text"],
            "hovertemplate": "%{text}<extra></extra>",
            "marker": {"color": colour, "size": 9, "symbol": symbol,
                       "line": {"color": theme.BG, "width": 1}},
        }

    data = [
        _trace(full, "full size", theme.GREEN, "circle"),
        _trace(reduced, "reduced", theme.AMBER, "diamond"),
        _trace(held, "held", theme.CYAN, "square"),
    ]

    good, fair = pipe.colour_thresholds()
    layout = _base_layout(
        showlegend=True,
        legend={"orientation": "h", "y": 1.12, "x": 0,
                "font": {"color": theme.MUTED, "size": 10},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"l": 52, "r": 16, "t": 36, "b": 46},
        xaxis=_axis(title={"text": f"max {basis} correlation to a holding",
                           "font": {"color": theme.MUTED, "size": 11}},
                    range=[-0.35, 1.02], tickformat="+.1f"),
        yaxis=_axis(title={"text": "composite",
                           "font": {"color": theme.MUTED, "size": 11}},
                    range=[0, 10.4]),
        shapes=[
            # The threshold, which moves with the slider.
            {"type": "line", "xref": "x", "yref": "paper",
             "x0": threshold, "x1": threshold, "y0": 0, "y1": 1,
             "line": {"color": theme.AMBER, "width": 1, "dash": "dash"}},
            # The score bands, so a point's height is readable against the
            # same cutoffs the table colours by.
            {"type": "line", "xref": "paper", "yref": "y",
             "x0": 0, "x1": 1, "y0": good, "y1": good,
             "line": {"color": theme.PANEL_BORDER, "width": 1, "dash": "dot"}},
            {"type": "line", "xref": "paper", "yref": "y",
             "x0": 0, "x1": 1, "y0": fair, "y1": fair,
             "line": {"color": theme.PANEL_BORDER, "width": 1, "dash": "dot"}},
        ],
        annotations=[
            {"x": threshold, "y": 1.02, "xref": "x", "yref": "paper",
             "text": f"threshold {threshold:.2f}", "showarrow": False,
             "font": {"color": theme.AMBER, "size": 10},
             "xanchor": "left", "yanchor": "bottom"},
        ],
    )
    return {"data": data, "layout": layout}


# ─── portfolio fit ───────────────────────────────────────────────────────

def portfolio_fit(scan, selected, sizing_by_ticker, use_high=False) -> dict:
    """Stacked bars by sector: holdings now, and what the selection would add.

    Section 6 asks which midpoint is used, so the trace is named for it and
    the caller states it in the panel caption. Weight for a holding is its
    share of cost basis WITHIN its own currency - never across, because
    section 12 forbids summing mixed currencies without conversion, and the
    currency bar below says so in as many words.
    """
    holdings = scan.holdings if scan else []
    sectors = {}

    # Current holdings, by sector, as a share of cost basis per currency.
    totals = {}
    for holding in holdings:
        currency = scan.holding_currency(holding)
        shares = pipe.num(holding.get("shares")) or 0.0
        basis = pipe.num(holding.get("cost_basis")) or 0.0
        totals[currency] = totals.get(currency, 0.0) + shares * basis

    for holding in holdings:
        ticker = holding.get("ticker")
        currency = scan.holding_currency(holding)
        shares = pipe.num(holding.get("shares")) or 0.0
        basis = pipe.num(holding.get("cost_basis")) or 0.0
        total = totals.get(currency) or 0.0
        if total <= 0:
            continue
        candidate = scan.by_ticker.get(ticker) or {}
        sector = candidate.get("sector") or "unknown sector"
        weight = 100.0 * (shares * basis) / total
        entry = sectors.setdefault(sector, {"held": 0.0, "add": 0.0,
                                            "held_names": [], "add_names": []})
        entry["held"] += weight
        entry["held_names"].append(f"{ticker} {weight:.1f}% of {currency}")

    # What the selection would add, at the adjusted midpoint (or high).
    for ticker in (selected or ()):
        candidate = scan.by_ticker.get(ticker) if scan else None
        if not candidate or candidate.get("already_held"):
            continue
        live = (sizing_by_ticker or {}).get(ticker) or candidate.get("sizing") or {}
        low = pipe.num(live.get("adjusted_low_pct"))
        high = pipe.num(live.get("adjusted_high_pct"))
        if low is None and high is None:
            continue
        if use_high:
            value = high if high is not None else low
        elif low is not None and high is not None:
            value = (low + high) / 2.0
        else:
            value = low if low is not None else high
        sector = candidate.get("sector") or "unknown sector"
        entry = sectors.setdefault(sector, {"held": 0.0, "add": 0.0,
                                            "held_names": [], "add_names": []})
        entry["add"] += value
        entry["add_names"].append(f"{ticker} +{value:.1f}%")

    if not sectors:
        return empty("no holdings and nothing selected - pick candidates in "
                     "the ratings table to see what they would add")

    order = sorted(sectors, key=lambda s: -(sectors[s]["held"] + sectors[s]["add"]))
    held_values = [round(sectors[s]["held"], 2) for s in order]
    add_values = [round(sectors[s]["add"], 2) for s in order]

    data = [
        {"type": "bar", "orientation": "h", "name": "held now",
         "y": order, "x": held_values,
         "marker": {"color": theme.CYAN},
         "text": ["<br>".join(sectors[s]["held_names"]) or "nothing held"
                  for s in order],
         "hovertemplate": "%{y}<br>held %{x:.1f}%<br>%{text}<extra></extra>"},
        {"type": "bar", "orientation": "h",
         "name": "selection would add",
         "y": order, "x": add_values,
         "marker": {"color": theme.AMBER},
         "text": ["<br>".join(sectors[s]["add_names"]) or "nothing selected"
                  for s in order],
         "hovertemplate": "%{y}<br>would add %{x:.1f}%<br>%{text}<extra></extra>"},
    ]

    layout = _base_layout(
        barmode="stack",
        showlegend=True,
        legend={"orientation": "h", "y": 1.14, "x": 0,
                "font": {"color": theme.MUTED, "size": 10},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"l": 128, "r": 18, "t": 38, "b": 40},
        xaxis=_axis(title={"text": "% of portfolio",
                           "font": {"color": theme.MUTED, "size": 11}},
                    ticksuffix="%"),
        yaxis=_axis(showgrid=False, automargin=True,
                    tickfont={"color": theme.CREAM, "size": 10}),
    )
    return {"data": data, "layout": layout}


def currency_split(scan) -> dict:
    """The CAD/USD bar, explicitly not converted (section 6 and 12)."""
    holdings = scan.holdings if scan else []
    if not holdings:
        return empty("no holdings on file")

    by_currency = {}
    for holding in holdings:
        currency = scan.holding_currency(holding)
        shares = pipe.num(holding.get("shares")) or 0.0
        basis = pipe.num(holding.get("cost_basis")) or 0.0
        by_currency[currency] = by_currency.get(currency, 0.0) + shares * basis

    order = sorted(by_currency)
    data = [{
        "type": "bar", "orientation": "h",
        "y": ["cost basis"] * len(order),
        "x": [round(by_currency[c], 2) for c in order],
        "name": "cost basis",
        "marker": {"color": [theme.CYAN if c == "CAD" else theme.GREEN
                             for c in order]},
        "text": [f"{c} {by_currency[c]:,.0f}" for c in order],
        "textposition": "inside",
        "insidetextfont": {"color": theme.BG, "size": 10},
        "hovertemplate": "%{text}<extra></extra>",
    }]
    layout = _base_layout(
        barmode="stack",
        margin={"l": 72, "r": 18, "t": 30, "b": 28},
        height=96,
        xaxis=_axis(visible=False),
        yaxis=_axis(showgrid=False, tickfont={"color": theme.MUTED, "size": 10}),
        annotations=[{
            "text": "mixed currency, not converted",
            "showarrow": False, "xref": "paper", "yref": "paper",
            "x": 0, "y": 1.3, "xanchor": "left",
            "font": {"color": theme.AMBER, "size": 10},
        }],
    )
    return {"data": data, "layout": layout}


# ─── clusters, as cards rather than a chart ──────────────────────────────

def clusters_html(scan) -> str:
    """Cluster cards: resolution, members with the winner marked, and why.

    A table of cards rather than a plot, because what matters per cluster is
    the verdict and the sentence explaining it - neither of which is a
    position on an axis. Rendered in the web panel so the correlation
    dropdown keeps one body.

    The sector-fund suggestion is scan_report's own: SECTOR_ETF_US and
    SECTOR_ETF_CA, picked by the cluster's majority sector and whether most
    of its members are CAD-quoted. Reused rather than re-tabulated so a new
    sector mapping in the pipeline shows up here too.
    """
    clusters = scan.clusters if scan else []
    standalone = scan.standalone if scan else []

    if not clusters and not standalone:
        reason = "no clustered.json yet - the clustering stage has not run"
        if scan and scan.clustered:
            reason = ((scan.clustered or {}).get("note")
                      or "this scan found no correlation clusters")
        return f'<div class="note">{_escape(reason)}</div>'

    out = [_cluster_css()]

    if (scan.clustered or {}).get("insufficient_data_for_clustering"):
        note = (scan.clustered or {}).get("note") or ""
        out.append(f'<div class="warn">{_escape(note)}</div>')

    for cluster in clusters:
        resolution = cluster.get("resolution") or "industry_wide"
        winner = cluster.get("winner")
        members = cluster.get("members") or []
        scores = cluster.get("scores") or {}

        tint = theme.CYAN if resolution == "industry_wide" else theme.GREEN
        out.append('<div class="card">')
        out.append(
            f'<div class="head">'
            f'<span class="res" style="color:{tint};border-color:{tint}">'
            f'{_escape(resolution)}</span>'
            f'<span class="meta">{len(members)} names'
            f' &middot; avg corr {_num(cluster.get("avg_correlation"))}'
            f' &middot; dispersion {_num(cluster.get("dispersion"))}</span>'
            f'</div>')

        rows = []
        for ticker in members:
            score = pipe.num(scores.get(ticker))
            is_winner = (ticker == winner)
            demoted = ticker in (cluster.get("demoted_peers") or [])
            colour = theme.score_colour(score)
            mark = "pick" if is_winner else ("demoted" if demoted else "")
            rows.append(
                f'<div class="row{" win" if is_winner else ""}">'
                f'<span class="tk">{_escape(ticker)}</span>'
                f'<span class="vn">{_escape(venue(ticker))}</span>'
                f'<span class="sc" style="color:{colour}">'
                f'{"n/a" if score is None else f"{score:.2f}"}</span>'
                f'<span class="mk">{mark}</span>'
                f'</div>')
        out.append('<div class="rows">' + "".join(rows) + '</div>')

        note = cluster.get("resolution_note")
        if note:
            out.append(f'<div class="why">{_escape(note)}</div>')

        if resolution == "industry_wide":
            suggestion = _sector_fund(scan, members)
            if suggestion:
                out.append(f'<div class="etf">{_escape(suggestion)}</div>')

        out.append('</div>')

    if standalone:
        notes = scan.standalone_notes
        out.append('<div class="card">')
        out.append('<div class="head"><span class="res" '
                   f'style="color:{theme.MUTED};border-color:{theme.PANEL_BORDER}">'
                   f'standalone</span><span class="meta">{len(standalone)} names, '
                   'in no genuine correlation cluster</span></div>')
        rows = []
        for ticker in standalone:
            rows.append(
                f'<div class="row"><span class="tk">{_escape(ticker)}</span>'
                f'<span class="why2">'
                f'{_escape(notes.get(ticker) or "no significant loading on any mode")}'
                f'</span></div>')
        out.append('<div class="rows">' + "".join(rows) + '</div>')
        out.append('</div>')

    return "".join(out)


def _sector_fund(scan, members):
    """scan_report's sector-ETF suggestion, with its ANSI codes removed."""
    if not (pipe.ready() and pipe.sr is not None):
        return None
    try:
        line = pipe.sr.sector_etf_line(list(members), scan.by_ticker)
    except Exception:                               # noqa: BLE001
        return None
    if not line:
        return None
    import re
    return re.sub(r"\033\[[0-9;]*m", "", str(line)).strip()


def _num(value, digits=2) -> str:
    number = pipe.num(value)
    return "n/a" if number is None else f"{number:.{digits}f}"


def _escape(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _cluster_css() -> str:
    """The cards' stylesheet.

    A function, not a module constant: it interpolates theme.font_families(),
    which reaches into QFontDatabase and so needs a QApplication to exist.
    Computing it at import time made importing this module - from a test, or
    from anything that runs before the app is constructed - emit a Qt warning
    and fall back to whatever font Qt guesses.
    """
    return f"""<style>
      .wrap {{ padding: 0; }}
      body {{ overflow-y: auto !important; }}
      .card {{
        border: 1px solid {theme.PANEL_BORDER};
        margin: 0 0 8px 0; padding: 8px 10px;
        background: {theme.BG};
      }}
      .head {{ display: flex; align-items: baseline; gap: 8px;
               margin-bottom: 6px; flex-wrap: wrap; }}
      .res {{ font-size: 10px; border: 1px solid; padding: 1px 5px;
              text-transform: lowercase; }}
      .meta {{ color: {theme.MUTED}; font-size: 10px; }}
      .rows {{ display: flex; flex-direction: column; }}
      .row {{ display: grid; grid-template-columns: 78px 58px 48px 1fr;
              gap: 6px; padding: 2px 0;
              border-bottom: 1px solid {theme.ROW_LINE}; align-items: baseline; }}
      .row.win {{ background: {theme.SELECTED}; }}
      .tk {{ color: {theme.CREAM}; }}
      .vn {{ color: {theme.FAINT}; font-size: 10px; }}
      .sc {{ text-align: right; }}
      .mk {{ color: {theme.AMBER}; font-size: 10px; }}
      .why, .why2 {{ color: {theme.MUTED}; font-size: 10px; }}
      .why {{ margin-top: 6px; }}
      .etf {{ color: {theme.AMBER}; font-size: 11px; margin-top: 5px; }}
      .note {{ color: {theme.MUTED}; padding: 12px 2px; }}
      .warn {{ color: {theme.AMBER}; padding: 6px 2px; }}
      .row, .tk, .sc {{ font-family: {theme.font_families()['mono']}; font-size: 11px; }}
    </style>"""
