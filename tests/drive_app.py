#!/usr/bin/env python3
"""Open the real window offscreen and exercise the interactions in section 7."""
import sys, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from jos import state as state_module, theme, pipeline as pipe
state_module.init()
settings = state_module.Settings()
pipe.bootstrap()

app = QApplication(sys.argv[:1])
theme.load_fonts()
app.setStyleSheet(theme.app_stylesheet())

from jos.window import MainWindow
from jos import sizing as sizing_module

notes = state_module.Notes()
win = MainWindow(settings, notes)
win.resize(1280, 900)
win.show()

failures = []
def check(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not condition else ""))
    if not condition:
        failures.append(label)

def run():
    try:
        print("\n== data loaded ==")
        check("scan loaded", win.scan is not None and win.scan.ok)
        n = len(win.scan.candidates)
        check("candidates loaded", n > 0, f"got {n}")
        check("candidates table populated", win.candidates.model.rowCount() == n)
        check("holdings table populated",
              win.holdings.table.rowCount() == len(win.scan.holdings),
              f"{win.holdings.table.rowCount()} vs {len(win.scan.holdings)}")
        check("freshness text set", bool(win.freshness.text()))
        check("status chip set", bool(win.status_chip.text()), win.status_chip.text())
        print(f"       chip: {win.status_chip.text()!r}")

        print("\n== sizing strip defaults come from params ==")
        v = win.sizing_strip.values()
        print(f"       {v}")
        check("threshold from scan params", abs(v["threshold"] - 0.70) < 1e-9)
        check("factor from scan params", abs(v["reduction_factor"] - 0.50) < 1e-9)
        check("basis from scan params", v["basis"] == "raw")
        check("mode from scan params", v["flat"] is False)
        check("is_default true at start", win.sizing_strip.is_default())

        print("\n== slider moves recompute sizing ==")
        before = dict(win._sizing)
        cut_before = sum(1 for s in before.values() if s.get("correlation_adjusted"))
        win.sizing_strip.set_values(threshold=0.95, emit=True)
        app.processEvents()
        cut_after = sum(1 for s in win._sizing.values() if s.get("correlation_adjusted"))
        print(f"       cuts at 0.70 -> {cut_before}, at 0.95 -> {cut_after}")
        check("raising threshold reduces cuts", cut_after < cut_before)
        check("strip flags drift from scan", not win.sizing_strip.is_default())

        win.sizing_strip.set_values(threshold=0.30, emit=True)
        app.processEvents()
        cut_low = sum(1 for s in win._sizing.values() if s.get("correlation_adjusted"))
        print(f"       cuts at 0.30 -> {cut_low}")
        check("lowering threshold increases cuts", cut_low > cut_before)

        print("\n== held names are never correlation-cut ==")
        held = [c["ticker"] for c in win.scan.candidates if c.get("already_held")]
        check("some held candidates exist", len(held) > 0, str(held))
        bad = [t for t in held if win._sizing[t].get("correlation_adjusted")]
        check("no held name is correlation-cut even at threshold 0.30", not bad, str(bad))

        print("\n== the NaN/None-only pair does not crash or cut ==")
        xyz = win._sizing.get("XYZ.V", {})
        check("XYZ.V (only unusable pairs) not cut", not xyz.get("correlation_adjusted"))
        check("XYZ.V note explains why", "no usable" in (xyz.get("note") or ""), xyz.get("note"))

        print("\n== basis + flat mode ==")
        win.sizing_strip.set_values(threshold=0.70, basis="cleaned", emit=True)
        app.processEvents()
        check("basis switch recomputes", win._sizing["BMO.TO"]["correlation_basis"] == "cleaned")
        win.sizing_strip.set_values(flat=True, emit=True)
        app.processEvents()
        s = win._sizing["BMO.TO"]
        if s.get("correlation_adjusted"):
            check("flat mode applies the whole factor",
                  abs(s["correlation_scale"] - 0.5) < 1e-6, str(s["correlation_scale"]))
        win.sizing_strip.reset()
        app.processEvents()
        check("reset returns to scan params", win.sizing_strip.is_default())

        print("\n== dropdowns switch instantly and are remembered ==")
        for key in ("holdings", "changes", "movers", "candidates"):
            win.ratings_panel.set_view(key)
            app.processEvents()
            check(f"ratings -> {key}", win.ratings_panel.current_view() == key
                  and settings.view("ratings") == key)
        for key in ("scatter", "clusters", "heatmap"):
            win.correlation_panel.set_view(key)
            app.processEvents()
            check(f"correlation -> {key}", win.correlation_panel.current_view() == key
                  and settings.view("correlation") == key)
        for key in ("macro", "earnings"):
            win.calendar_panel.set_view(key)
            app.processEvents()
            check(f"calendar -> {key}", win.calendar_panel.current_view() == key)

        print("\n== selection feeds ticker detail ==")
        ok = win.candidates.select_ticker("MSFT")
        app.processEvents()
        check("selected MSFT", ok and win.detail.subject() == "MSFT", str(win.detail.subject()))
        check("detail shows the name", "Microsoft" in win.detail.name_label.text(),
              win.detail.name_label.text())
        check("detail guide populated", bool(win.detail.guide_label.text()))
        check("selection count shown", "selected" in win.selection_label.text(),
              win.selection_label.text())

        print("\n== dims use the evaluator's real keys ==")
        rec = win.scan.scored_by_ticker["MSFT"]
        keys = pipe.dims_order(rec["dims"])
        print(f"       {keys}")
        check("six dims in evaluator order",
              keys == ["Valuation","Profitability","Growth","Health","Momentum","Frameworks"])

        print("\n== reload keeps the person's place (section 7) ==")
        win.candidates.select_ticker("CNQ.TO")
        app.processEvents()
        state = win.candidates.view_state()
        win._reload()
        app.processEvents()
        check("selection survives reload", win.candidates.selected_tickers() == state["selected"],
              f"{win.candidates.selected_tickers()} vs {state['selected']}")
        check("detail subject survives reload", win.detail.subject() == "CNQ.TO")
        check("dropdown choice survives reload", win.correlation_panel.current_view() == "heatmap")

        print("\n== sector filter chip ==")
        win.candidates.set_sector_filter("Technology")
        app.processEvents()
        shown = win.candidates.proxy.rowCount()
        check("filter narrows the table", 0 < shown < len(win.scan.candidates),
              f"{shown} rows")
        check("filter chip visible", win.candidates.sector_chip.isVisible())
        win.candidates.set_sector_filter(None)
        app.processEvents()
        check("clearing restores all rows",
              win.candidates.proxy.rowCount() == len(win.scan.candidates))

        print("\n== sorting ==")
        from jos.views.candidates import COL_SCORE, TICKER_ROLE
        from PyQt6.QtCore import Qt
        win.candidates.table.sortByColumn(COL_SCORE, Qt.SortOrder.DescendingOrder)
        app.processEvents()
        first = win.candidates.proxy.index(0, COL_SCORE).data(TICKER_ROLE)
        top = max(win.scan.candidates, key=lambda c: c["composite"] or 0)["ticker"]
        check("descending score sort puts the best first", first == top, f"{first} vs {top}")

        print("\n== charts build without error ==")
        from jos.views import charts
        import json
        v = win.sizing_strip.values()
        for name, fig in (
            ("scatter", charts.score_vs_correlation(win.scan.candidates, win._sizing,
                                                    v["threshold"], v["basis"])),
            ("portfolio fit", charts.portfolio_fit(win.scan, ["MSFT","NVDA"], win._sizing)),
            ("currency split", charts.currency_split(win.scan)),
        ):
            js = json.dumps(fig, default=str, allow_nan=False)
            check(f"{name} serialises for Plotly", len(js) > 100, f"{len(js)} chars")
        html = charts.clusters_html(win.scan)
        check("clusters html built", "pick_winner" in html or "industry_wide" in html)
        check("clusters html names a sector fund", "Sector ETF" in html or "XLK" in html
              or "XFN" in html, "no ETF line")

        print("\n== holdings view uses the pipeline's own verdicts ==")
        from jos.views.holdings import COL_STATUS, COL_WEIGHT
        statuses = [win.holdings.table.item(r, COL_STATUS).text()
                    for r in range(win.holdings.table.rowCount())]
        print(f"       {statuses}")
        check("every holding has a status", all(statuses))
        check("statuses come from the verdict vocabulary",
              set(statuses) <= {"intact","watch","review","no data",
                                "building history"}, str(set(statuses)))
        check("mixed-currency warning shown", "mixed currency" in win.holdings.currency_note.text(),
              win.holdings.currency_note.text())

        print("\n== notes round-trip ==")
        win.detail.show_ticker("MSFT")
        win.detail.notes_box.setPlainText("watching for a pullback")
        win.detail.revisit_box.setText("2026-11-01")
        win.detail.flush_notes()
        reread = state_module.Notes()
        entry = reread.get("MSFT")
        check("note text saved", entry.get("text") == "watching for a pullback", str(entry))
        check("revisit date saved", entry.get("revisit") == "2026-11-01")
        check("saved_at recorded", bool(entry.get("saved_at")))

        print("\n== history snapshot ==")
        from jos import state as st
        path, doc = st.latest_snapshot()
        check("snapshot written for this scan", path is not None, "none")
        if doc:
            check("snapshot matches the scan",
                  doc.get("generated_at") == win.scan.generated_at)
        again = st.take_snapshot(win.scan.sized)
        check("same scan is not snapshotted twice", again is False)

        print("\n== closing with a job in flight does not crash ==")
        from jos import workers
        import time as _t
        slow = workers.submit(lambda: _t.sleep(3) or "late",
                              on_done=lambda r: print("       (late result arrived)"))
        app.processEvents()
        check("a job is in flight", len(workers._live) >= 1, str(len(workers._live)))
        win.close()
        app.processEvents()
        check("live jobs cleared on close", len(workers._live) == 0)
        check("in-flight job was cancelled", slow._cancelled)

    except Exception:
        traceback.print_exc()
        failures.append("exception")
    finally:
        print(f"\n{'='*62}")
        print(f"RESULT: {len(failures)} failure(s)" + (f": {failures}" if failures else " - all passed"))
        print(f"{'='*62}")
        app.quit()

QTimer.singleShot(600, run)
sys.exit(app.exec())
