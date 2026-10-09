#!/usr/bin/env python3
"""Non-UI checks: no-data handling, the refresh lock, freshness, venue labels.

Covers the parts of section 9's test plan that do not need a reboot:
  item 4  a logon and a wake arriving together produce ONE scan (the lock)
  item 5  data fresher than the minimum interval -> no scan runs
  item 6  a killed autorun does not leave the lock stuck forever
"""
import datetime as dt, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jos import pipeline as pipe
pipe.bootstrap()

from jos import refresh as refresh_module, state as state_module, sizing
from jos.datastore import Scan, venue, venue_coverage, is_canadian, describe_age
from jos.paths import DataPaths

fails = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: fails.append(label)

FIX = Path(__file__).resolve().parent / "fakedata"

print("\n== absent data reads as a sentence, not an error (section 4) ==")
with tempfile.TemporaryDirectory() as empty:
    paths = DataPaths(empty)
    scan = Scan(paths)
    check("Scan builds over an empty directory", scan is not None)
    check("ok is False", scan.ok is False)
    check("empty_reason explains itself", "scan running or not run" in scan.empty_reason,
          scan.empty_reason)
    check("holdings is an empty list", scan.holdings == [])
    check("sizing defaults fall back to the pipeline's constants",
          scan.sizing_defaults()["threshold"] == pipe.ps.CORR_THRESHOLD)
    check("freshness line does not crash", isinstance(scan.freshness_line(), str))
    check("cluster_order is empty", scan.cluster_order() == [])

print("\n== a half-written / corrupt file keeps the app alive ==")
with tempfile.TemporaryDirectory() as broken:
    d = Path(broken)
    (d / "sized_candidates.json").write_text('{"candidates": [{"ticker": "AA"')  # truncated
    (d / "clustered.json").write_text("not json at all")
    scan = Scan(DataPaths(d))
    check("truncated JSON reads as absent", scan.sized is None)
    check("garbage JSON reads as absent", scan.clustered is None)
    check("still reports a reason", bool(scan.empty_reason))

print("\n== venue labelling: 'is it TSX only' answered from the data ==")
check(".TO -> TSX", venue("RY.TO") == "TSX")
check(".V -> TSXV", venue("XYZ.V") == "TSXV")
check(".CN -> CSE", venue("ABC.CN") == "CSE")
check(".NE -> Cboe CA", venue("AAPL.NE") == "Cboe CA")
check("bare -> US", venue("MSFT") == "US")
check("is_canadian on each venue",
      all(is_canadian(t) for t in ("A.TO","B.V","C.CN","D.NE")) and not is_canadian("MSFT"))
check("TSX only is named as TSX only",
      venue_coverage(["RY.TO","TD.TO","BMO.TO"]) == "TSX only",
      venue_coverage(["RY.TO","TD.TO","BMO.TO"]))
check("TSX + TSXV without US says so",
      venue_coverage(["RY.TO","XYZ.V"]) == "TSX + TSXV, no US",
      venue_coverage(["RY.TO","XYZ.V"]))
check("US only is named", venue_coverage(["MSFT","AAPL"]) == "US only")
check("mixed names both", venue_coverage(["MSFT","RY.TO"]) == "US + TSX",
      venue_coverage(["MSFT","RY.TO"]))
check("empty is not a crash", venue_coverage([]) == "no listings")

print("\n== the brief's naive sizing WOULD have disagreed ==")
scan = Scan(DataPaths(FIX))
naive_diffs, conviction_names = [], []
for c in scan.candidates:
    st = c.get("sizing") or {}
    cs = st.get("conviction_scale")
    if c.get("already_held") or cs in (None, 1.0):
        continue
    conviction_names.append((c["ticker"], cs))
    pairs = st.get("correlations") or {}
    usable = {h: v.get("raw") for h, v in pairs.items()
              if isinstance(v, dict) and isinstance(v.get("raw"), (int, float))}
    if not usable:
        continue
    worst = max(usable, key=lambda h: usable[h])
    # The brief's sketch: correlation scale only, conviction ignored.
    nscale, _ = pipe.ps.sizing_scale(usable[worst], 0.70, 0.50, flat=False)
    stored = st.get("scale")
    if abs(nscale - stored) > 0.005:
        naive_diffs.append((c["ticker"], round(nscale, 4), stored))
print(f"       names with a conviction modifier: {conviction_names}")
print(f"       naive-vs-stored disagreements: {naive_diffs}")
check("at least one name carries a conviction modifier", len(conviction_names) > 0)
check("the naive formula disagrees with the pipeline on those", len(naive_diffs) > 0,
      "no disagreement found")
check("my recompute agrees with the pipeline everywhere",
      sizing.verify(scan.candidates, scan.params) == [])

print("\n== the refresh lock: one scan at a time (test-plan item 4) ==")
with tempfile.TemporaryDirectory() as tmp:
    lock = Path(tmp) / "refresh.lock"
    check("no lock to start", not refresh_module.lock_is_live(lock))
    check("first take succeeds", refresh_module.take_lock("logon", lock))
    check("lock now reads live", refresh_module.lock_is_live(lock))
    # A second trigger arriving at once (a wake right after a logon).
    check("a second take is refused", not refresh_module.take_lock("wake", lock))
    refresh_module.release_lock(lock)
    check("released", not refresh_module.lock_is_live(lock))
    check("take works again after release", refresh_module.take_lock("wake", lock))
    refresh_module.release_lock(lock)

print("\n== a killed autorun does not wedge it forever (item 6) ==")
with tempfile.TemporaryDirectory() as tmp:
    lock = Path(tmp) / "refresh.lock"
    # A lock from a PID that does not exist: the process died.
    state_module.write_json(
        {"pid": 999999, "reason": "logon",
         "started_at": dt.datetime.now().isoformat(timespec="seconds")}, lock)
    check("a dead PID's lock is not live", not refresh_module.lock_is_live(lock))
    check("and can be taken over", refresh_module.take_lock("manual", lock))
    refresh_module.release_lock(lock)
    # An ancient lock, whatever the PID says.
    old = (dt.datetime.now() - dt.timedelta(hours=5)).isoformat(timespec="seconds")
    state_module.write_json({"pid": os.getpid(), "reason": "logon",
                             "started_at": old}, lock)
    check("a stale lock is not live even with a live PID",
          not refresh_module.lock_is_live(lock))

print("\n== fresh data means no scan runs (item 5) ==")
paths = DataPaths(FIX)
check("fixture data counts as fresh within 60 min",
      refresh_module.data_is_fresh(paths, 60))
check("and not fresh against a 0-minute floor",
      not refresh_module.data_is_fresh(paths, 0))
with tempfile.TemporaryDirectory() as tmp:
    check("missing data is never 'fresh'",
          not refresh_module.data_is_fresh(DataPaths(tmp), 60))

check("fixtures exist for the refresh check", paths.sized.is_file(), str(paths.sized))
settings = state_module.Settings()
settings.set("min_minutes_between_scans", 600)
# Fresh data must short-circuit BEFORE the network probe. If it did not, an
# automatic refresh on a machine with no route out would sit in
# wait_for_network for network_wait_minutes on every single wake.
settings.set("network_wait_minutes", 0)
ok, message = refresh_module.refresh(settings, paths, reason="wake", manual=False)
print(f"       {message}")
check("an automatic refresh over fresh data skips the scan",
      ok and "no scan ran" in message, message)

print("\n== scan_enabled=false makes a machine view-only (section 9b) ==")
settings.set("scan_enabled", False)
ok, message = refresh_module.refresh(settings, paths, reason="logon", manual=True)
print(f"       {message}")
check("view-only machine refuses to scan", not ok and "never runs the pipeline" in message)
settings.set("scan_enabled", True)

print("\n== status transitions ==")
with tempfile.TemporaryDirectory() as tmp:
    sp = Path(tmp) / "status.json"
    for st in ("waiting_for_network", "refreshing", "idle", "failed"):
        state_module.write_status(st, stage="scan_report",
                                  last_error="no network" if st == "failed" else None,
                                  path=sp)
        back = state_module.read_status(sp)
        check(f"status '{st}' round-trips", back.get("state") == st)
    check("failed carries the reason",
          state_module.read_status(sp).get("last_error") == "no network")

print("\n== history: snapshot naming, dedupe, nearest, prune ==")
with tempfile.TemporaryDirectory() as tmp:
    h = Path(tmp)
    doc = {"generated_at": "2026-10-09T14:35:12", "candidates": []}
    check("snapshot name is YYYY-MM-DD_HHMM",
          state_module.snapshot_name(doc["generated_at"]) == "2026-10-09_1435",
          state_module.snapshot_name(doc["generated_at"]))
    check("first snapshot is written", state_module.take_snapshot(doc, h))
    check("the same scan is not filed twice", not state_module.take_snapshot(doc, h))
    doc2 = {"generated_at": "2026-10-09T18:05:00", "candidates": []}
    check("a new scan is filed", state_module.take_snapshot(doc2, h))
    # An old one, to prune.
    (h / "2026-01-02_0900.json").write_text('{"generated_at": "2026-01-02T09:00:00"}')
    removed = state_module.prune_snapshots(h, keep_days=90)
    check("an out-of-window snapshot is pruned", removed == 1, f"removed {removed}")
    check("the newest is never pruned", (h / "2026-10-09_1805.json").is_file())
    path, found = state_module.snapshot_nearest(0, h)
    check("nearest-to-today finds one", path is not None)

print("\n== describe_age wording ==")
for seconds, want in ((5, "just now"), (60*5, "5m ago"), (3600*3, "3h ago"),
                      (86400*4, "4d ago"), (None, "unknown")):
    got = describe_age(seconds)
    check(f"describe_age({seconds}) == {want!r}", got == want, got)

print(f"\n{'='*62}")
print(f"RESULT: {len(fails)} failure(s)" + (f": {fails}" if fails else " - all passed"))
print(f"{'='*62}")
sys.exit(1 if fails else 0)
