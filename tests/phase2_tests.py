#!/usr/bin/env python3
"""Phase 2: sector ratings, changes since last scan, lookup, holdings health.

Run tests/make_fixtures.py then tests/make_history.py first - the changes view
and the 30-day health comparison both need dated snapshots to compare against.
"""
import datetime as dt
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from jos import pipeline as pipe
from jos import state as state_module, theme

state_module.init()
settings = state_module.Settings()
pipe.bootstrap()

app = QApplication(sys.argv[:1])
theme.load_fonts()
app.setStyleSheet(theme.app_stylesheet())

from jos import changes as changes_module, health as health_module
from jos import sectors as sectors_module, earnings as earnings_module
from jos.window import MainWindow

fails = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(label)

notes = state_module.Notes()
win = MainWindow(settings, notes)
win.resize(1400, 950)
win.show()


def run():
    try:
        scan = win.scan

        # ─────────────────────────────────────────── sector ratings
        print("\n== sector ratings ==")
        bases = sectors_module.available_bases(scan.candidates)
        keys = [k for k, _ in bases]
        print(f"       bases offered: {keys}")
        check("composite is the first basis", keys[0] == "composite")
        check("bases are built from the real dims, not invented",
              set(keys[1:]) <= {"Valuation","Profitability","Growth","Health",
                                "Momentum","Frameworks"}, str(keys[1:]))
        check("no invented 'quality' basis", "quality" not in [k.lower() for k in keys])

        rows = sectors_module.summarise(scan.candidates, "composite")
        print(f"       {[(r['sector'], r['average'], r['count']) for r in rows]}")
        check("one row per sector", len(rows) == len({c.get('sector') for c in scan.candidates}))
        check("sorted by average, best first",
              all((rows[i]['average'] or 0) >= (rows[i+1]['average'] or 0)
                  for i in range(len(rows)-1)))
        check("counts add up to the candidate list",
              sum(r['count'] for r in rows) == len(scan.candidates))
        check("each row names its best ticker", all(r['best'] for r in rows if r['measured']))
        fin = next(r for r in rows if r['sector'] == 'Financial Services')
        best_fin = max((c for c in scan.candidates if c['sector']=='Financial Services'),
                       key=lambda c: c['composite'])
        check("best ticker is really the best", fin['best'] == best_fin['ticker'],
              f"{fin['best']} vs {best_fin['ticker']}")

        # unmeasured handling
        dims_basis = next((k for k in keys[1:]), None)
        if dims_basis:
            drows = sectors_module.summarise(scan.candidates, dims_basis)
            check(f"averaging on {dims_basis} reports its measured count",
                  all(r['measured'] + r['unmeasured'] == r['count'] for r in drows))

        print("\n== clicking a sector filters the table ==")
        win.ratings_panel.set_view("holdings")       # start on another view
        app.processEvents()
        win.sectors.sector_clicked.emit("Technology")
        app.processEvents()
        check("candidates table is filtered", win.candidates.proxy.rowCount()
              == sum(1 for c in scan.candidates if c['sector'] == 'Technology'),
              str(win.candidates.proxy.rowCount()))
        check("ratings dropdown switched to candidates",
              win.ratings_panel.current_view() == "candidates")
        check("filter chip is showing", win.candidates.sector_chip.isVisible())
        check("chip names the sector", "Technology" in win.candidates.sector_chip.text())
        check("sector row is marked active", win.sectors._active == "Technology")

        win.sectors.sector_clicked.emit("Technology")   # click again
        app.processEvents()
        check("clicking the same sector clears it", win.candidates.proxy.rowCount()
              == len(scan.candidates))
        check("active mark cleared", win.sectors._active is None)

        # ─────────────────────────────────────── changes since last scan
        print("\n== changes since last scan ==")
        result = win.changes._result
        check("a comparison was available", result.get("available"),
              result.get("reason"))
        counts = result.get("counts") or {}
        print(f"       counts: {counts}")
        check("every kind is counted", set(counts) == set(changes_module.KINDS))
        check("there are rows", sum(counts.values()) > 0)
        check("AMD shows as new (absent from the snapshot)",
              any(r['ticker']=='AMD' and r['kind']=='new' for r in result['rows']))
        check("SU.TO shows as new", any(r['ticker']=='SU.TO' and r['kind']=='new'
                                       for r in result['rows']))
        check("GONE.TO shows as dropped",
              any(r['ticker']=='GONE.TO' and r['kind']=='dropped' for r in result['rows']))

        bns = next((r for r in result['rows'] if r['ticker']=='BNS.TO'), None)
        if bns:
            why = "; ".join(bns['reasons'])
            print(f"       BNS.TO why: {why}")
            check("BNS.TO names the trend turning", "trend now failing" in why, why)
            check("BNS.TO names the pattern change", "value-trap" in why or "pattern" in why, why)

        xyz = next((r for r in result['rows'] if r['ticker']=='XYZ.V'), None)
        if xyz:
            why = "; ".join(xyz['reasons'])
            print(f"       XYZ.V why: {why}")
            check("XYZ.V names the liquidity filter", "liquidity" in why, why)

        gone = next(r for r in result['rows'] if r['ticker']=='GONE.TO')
        print(f"       GONE.TO why: {gone['reasons']}")
        check("a dropped name gets a real reason, not a shrug",
              bool(gone['reasons']) and "no longer in the scan's shortlist" not in gone['reasons'][0],
              str(gone['reasons']))

        check("every row has at least one reason",
              all(r['reasons'] for r in result['rows']))
        check("rows sorted biggest mover first",
              all(abs(result['rows'][i].get('delta') or 0) >= abs(result['rows'][i+1].get('delta') or 0)
                  for i in range(len(result['rows'])-1)
                  if result['rows'][i].get('delta') and result['rows'][i+1].get('delta')))

        print("\n== change filter chips ==")
        win.ratings_panel.set_view("changes")
        app.processEvents()
        total = win.changes.table.rowCount()
        for kind in ("new", "up", "down", "dropped"):
            win.changes._set_filter(kind)
            app.processEvents()
            shown = win.changes.table.rowCount()
            check(f"filter '{kind}' shows {counts.get(kind,0)}",
                  shown == counts.get(kind, 0), f"{shown} rows")
        win.changes._set_filter("all")
        app.processEvents()
        check("filter 'all' restores every row", win.changes.table.rowCount() == total)

        print("\n== no earlier snapshot reads as a sentence ==")
        empty = changes_module.compare(scan.sized, None)
        check("unavailable when there is no snapshot", not empty["available"])
        check("and says why plainly", "no earlier scan" in empty["reason"], empty["reason"])
        same = changes_module.compare(scan.sized, scan.sized)
        check("comparing a scan to itself is refused", not same["available"])
        check("and says so", "same scan" in same["reason"], same["reason"])

        # ──────────────────────────────────────────── holdings health
        print("\n== holdings health ==")
        win.ratings_panel.set_view("holdings")
        app.processEvents()
        limits = health_module.thresholds(settings)
        print(f"       thresholds: {limits}")
        check("green threshold follows the evaluator, not the brief's 7.5",
              abs(limits['green_from'] - pipe.colour_thresholds()[0]) < 1e-9,
              str(limits['green_from']))
        check("history is deep enough for a verdict",
              state_module.history_days() >= limits['min_history_days'],
              str(state_module.history_days()))

        rows = win.holdings._rows
        print(f"       {[(r['ticker'], r['status'], r['delta']) for r in rows]}")
        check("a row per holding", len(rows) == len(scan.holdings))
        check("scored rows are not stuck on 'building history'",
              any(r['status'] in (health_module.REVIEW, health_module.WATCH,
                                  health_module.INTACT) for r in rows),
              str([r['status'] for r in rows]))
        check("statuses come from the documented set",
              {r['status'] for r in rows} <= {health_module.REVIEW,
                                              health_module.WATCH,
                                              health_module.INTACT,
                                              health_module.NO_DATA,
                                              health_module.BUILDING},
              str({r['status'] for r in rows}))
        scored_rows = [r for r in rows if r['status'] not in
                       (health_module.NO_DATA, health_module.BUILDING)]
        check("every row with a verdict has a 30-day baseline",
              all(r['before'] is not None for r in scored_rows),
              str([(r['ticker'], r['before']) for r in scored_rows]))
        check("every row explains its status", all(r['reasons'] for r in rows))

        print("\n== an unscored holding never reads as intact ==")
        enb = next((r for r in rows if r['ticker'] == 'ENB.TO'), None)
        check("the unscored holding is in the table", enb is not None)
        if enb:
            print(f"       ENB.TO: {enb['status']} <- {enb['reasons']}")
            check("it reads 'no data', not 'intact'",
                  enb['status'] == health_module.NO_DATA, enb['status'])
            check("and says it could not be scored",
                  any("could not be scored" in r for r in enb['reasons']),
                  str(enb['reasons']))

        td_row = next((r for r in rows if r['ticker']=='TD.TO'), None)
        if td_row:
            print(f"       TD.TO: {td_row['status']} <- {td_row['reasons']}")
            check("TD.TO fell enough to be review", td_row['status'] == health_module.REVIEW,
                  f"{td_row['status']} delta={td_row['delta']}")
            check("and the rule that fired is named",
                  any("down" in r for r in td_row['reasons']), str(td_row['reasons']))

        print("\n== the health rules in isolation ==")
        L = limits
        def st(now, before, days=40):
            return health_module.status_for(now, before, L, history_days=days)["status"]
        check("a 1.5 drop is review",
              st({"composite": 6.0}, {"composite": 7.5}) == health_module.REVIEW)
        check("a 0.8 drop is watch",
              st({"composite": 6.7}, {"composite": 7.5}) == health_module.WATCH)
        check("a 0.2 drop is intact",
              st({"composite": 7.3}, {"composite": 7.5}) == health_module.INTACT)
        check("below the red band is review whatever the drop",
              st({"composite": 4.9}, {"composite": 5.0}) == health_module.REVIEW)
        check("trend turning failing is review",
              st({"composite": 7.5, "roa_trend": "deteriorating"},
                 {"composite": 7.5, "roa_trend": "flat"}) == health_module.REVIEW)
        check("debt turning rising is review",
              st({"composite": 7.5, "debt_trend": "increasing"},
                 {"composite": 7.5, "debt_trend": "stable"}) == health_module.REVIEW)
        check("a trend that was already failing does not re-fire",
              st({"composite": 7.5, "roa_trend": "deteriorating"},
                 {"composite": 7.5, "roa_trend": "deteriorating"}) == health_module.INTACT)
        green = L['green_from']
        check("crossing down out of green is watch",
              st({"composite": green - 0.1}, {"composite": green + 0.05}) == health_module.WATCH)
        check("thin history reads as building history",
              st({"composite": 4.0}, {"composite": 9.0}, days=3) == health_module.BUILDING)
        check("no baseline at all reads as building history",
              health_module.status_for({"composite": 7.0}, None, L,
                                       history_days=None)["status"] == health_module.BUILDING)
        check("no current composite reads as no data, never intact",
              st({"composite": None}, {"composite": 7.5}) == health_module.NO_DATA)
        check("an empty record reads as no data",
              st({}, {"composite": 7.5}) == health_module.NO_DATA)
        check("a baseline with no composite reads as building history",
              st({"composite": 7.0}, {"composite": None}) == health_module.BUILDING)

        print("\n== currency is never summed across ==")
        check("mixed currency is flagged", "mixed currency" in win.holdings.currency_note.text(),
              win.holdings.currency_note.text())
        from jos.views.holdings import COL_WEIGHT
        weights = []
        for r in range(win.holdings.table.rowCount()):
            text = win.holdings.table.item(r, COL_WEIGHT).text()
            if text.endswith("%"):
                weights.append(float(text[:-1]))
        print(f"       weights: {weights}")
        check("weights are per-currency shares, so they exceed 100 in total",
              abs(sum(weights) - 100.0) > 1.0, f"sum={sum(weights):.1f}")

        print("\n== earnings dates: unknown is never a guess ==")
        check("describe(None) is unknown",
              earnings_module.describe(None) == earnings_module.UNKNOWN)
        check("describe({}) is unknown",
              earnings_module.describe({}) == earnings_module.UNKNOWN)
        check("describe with no date is unknown",
              earnings_module.describe({"date": None}) == earnings_module.UNKNOWN)
        soon = (dt.date.today() + dt.timedelta(days=12)).isoformat()
        text = earnings_module.describe({"date": soon, "timing": "After Market Close"})
        print(f"       {text}")
        check("a real date reads as a date with timing and days", "in 12 days" in text
              and "after close" in text, text)
        today = earnings_module.describe({"date": dt.date.today().isoformat()})
        check("today reads as today", "today" in today, today)
        check("a bad date string is unknown, not a crash",
              earnings_module.describe({"date": "not-a-date"}) == earnings_module.UNKNOWN)

        # ─────────────────────────────────────────────────── lookup
        print("\n== lookup box ==")
        check("the box exists at the top of ticker detail",
              win.detail.lookup_box is not None)
        win.detail.lookup_box.setText("MSFT")
        win.detail._on_lookup()
        app.processEvents()
        check("a ticker already in the scan becomes the subject with no network",
              win.detail.subject() == "MSFT", str(win.detail.subject()))
        check("and its source says it came from the pipeline",
              "in pipeline" in win.detail.source.text(), win.detail.source.text())
        check("it is remembered in recent lookups",
              "MSFT" in (settings.get("recent_lookups") or []))

        print("\n== a looked-up ticker not in the scan ==")
        fake = {"ticker": "ZZZZ", "name": "Elsewhere Inc", "sector": "Technology",
                "quote_currency": "USD", "composite": 7.1, "rating": "BUY / ACCUMULATE",
                "dims": {"Valuation": 6.0, "Profitability": 7.5, "Growth": 7.0,
                         "Health": 8.0, "Momentum": 5.5, "Frameworks": 6.8},
                "frameworks": {"piotroski": {"score": 7, "max": 9, "label": "Strong"},
                               "altman_z": {"score": 3.2, "zone": "Safe"}},
                "roa_trend": "improving", "fcf_positive_years": 4,
                "divergence_pattern": "neutral", "warnings": []}
        win.detail.remember_external("ZZZZ", fake)
        win.detail.show_ticker("ZZZZ", source="looked up - evaluated just now")
        app.processEvents()
        check("the external record becomes the subject", win.detail.subject() == "ZZZZ")
        check("its name is drawn", "Elsewhere" in win.detail.name_label.text())
        check("source says looked up", "looked up" in win.detail.source.text(),
              win.detail.source.text())
        check("sizing lines say it is not in the scan",
              "not in the current scan" in win.detail.guide_label.text(),
              win.detail.guide_label.text())

        print("\n== recent-lookup chips re-open a subject ==")
        win.detail.set_recent(["MSFT", "NVDA"])
        app.processEvents()
        check("chips are built", win.detail._recent_row.count() >= 3)

        print("\n== a bare symbol offers its listings ==")
        win.detail.show_listing_choice([
            {"ticker": "SHOP", "name": "Shopify", "currency": "USD", "last_close": 120.0},
            {"ticker": "SHOP.TO", "name": "Shopify", "currency": "CAD", "last_close": 164.0}])
        app.processEvents()
        check("a choice row is offered", win.detail._choice_row.count() >= 3)
        win.detail.set_lookup_state("no listing found for NOPE", level="warn")
        check("an unknown symbol gets a plain message",
              "no listing found" in win.detail.lookup_state.text())

        print("\n== unchanged Phase 1 behaviour still holds ==")
        win.ratings_panel.set_view("candidates")
        app.processEvents()
        check("candidates table still populated",
              win.candidates.model.rowCount() == len(scan.candidates),
              f"{win.candidates.model.rowCount()} vs {len(scan.candidates)}")
        check("sizing still matches the scan",
              __import__("jos.sizing", fromlist=["verify"]).verify(
                  scan.candidates, scan.params) == [])
        win.sizing_strip.set_values(threshold=0.95, emit=True)
        app.processEvents()
        cuts = sum(1 for s in win._sizing.values() if s.get("correlation_adjusted"))
        check("sliders still drive the table", cuts == 0, str(cuts))
        win.sizing_strip.reset()
        app.processEvents()

        print("\n== reload keeps Phase 2 state too ==")
        win._set_sector("Energy")
        app.processEvents()
        win.candidates.select_ticker("CNQ.TO")
        app.processEvents()
        win._reload()
        app.processEvents()
        check("sector filter survives reload", win._sector_filter == "Energy")
        check("filter still applied to the table",
              win.candidates.proxy.rowCount()
              == sum(1 for c in scan.candidates if c['sector'] == 'Energy'))
        check("changes view still available after reload",
              win.changes._result.get("available"))
        win._set_sector(None)
        app.processEvents()

    except Exception:
        traceback.print_exc()
        fails.append("exception")
    finally:
        from jos import workers
        workers.shutdown(500)
        print(f"\n{'='*62}")
        print(f"RESULT: {len(fails)} failure(s)" + (f": {fails}" if fails else " - all passed"))
        print(f"{'='*62}")
        app.quit()


QTimer.singleShot(700, run)
sys.exit(app.exec())
