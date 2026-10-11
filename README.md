# jostocks

A PyQt6 desktop view over the stocks scan pipeline, with live position sizing.

It reads what the scan wrote and lets you move the correlation threshold and
reduction factor against it, re-sizing every candidate as you drag. The sizing
maths is the pipeline's own — `position_sizer` is imported, not reimplemented —
so what the window shows and what `run_scan.bat` prints agree.

**jostocks never writes to the pipeline's data files.** `holdings.json`,
`sized_candidates.json`, `clustered.json` and `scored_candidates.json` belong
to the pipeline; this app only ever reads them. Everything it owns lives in
`data/` beside this file. If you want to change your holdings, edit
`holdings.json` and run the scan — this app will notice the new file and
reload without a restart.

---

## Install

jostocks expects to sit **next to** the pipeline:

```
C:\Users\joey\stocks\              market_data.py, position_sizer.py, ...
C:\Users\joey\stocks\jostocks\     this folder
```

That is how it finds the pipeline — it looks in its own parent directory for
`market_data.py`. If your layout differs, set `pipeline_dir` in
`data/settings.json` or the `STOCKS_DIR` environment variable, and it will
say so clearly rather than failing silently.

```
python -m pip install -r requirements.txt
```

Then check what it can see before trusting it:

```
python jostocks.py --self-test
```

That prints the pipeline it found, the score thresholds it read from the
evaluator, which data files exist, how many candidates and holdings it can
see, which exchanges the scan actually covers, and whether its sizing agrees
with the scan. If the last line of that report says anything other than
`matches the scan`, read [Keeping in step with the pipeline](#keeping-in-step-with-the-pipeline).

## Launch

| How | What it does |
|---|---|
| `launch.bat` (double-click) | Opens the window. Uses `.venv\Scripts\pythonw.exe` if there is one. |
| `python jostocks.py` | Same, from a terminal. |
| `python jostocks.py --refresh` | Opens, and runs a scan if the data is stale. |
| Startup shortcut | Set up by `install_autostart.ps1`; see [Auto-update](#auto-update-and-autostart). |

Only one window opens. A second launch raises the one already running rather
than starting a second copy.

**This app does not scan on its own unless you ask it to.** The refresh button
runs the scan in the background, at below-normal priority, and the window
keeps showing the last good data while it works. For a scan in a terminal
where you can watch it, use `run_scan.bat` in the stocks folder.

---

## The panels

### sizing strip (always visible)

Four controls, and they re-size every not-held candidate with no network and
no pipeline run — `sized_candidates.json` already stores each candidate's raw
and cleaned correlation to every holding.

- **correlation threshold** — above this correlation with a holding, a
  candidate is treated as partly the same position and its size is cut.
- **reduction factor** — how much of the base size a fully correlated (1.0)
  duplicate loses.
- **basis: raw / cleaned** — `raw` is the sample correlation; `cleaned` is the
  same matrix after the Marchenko-Pastur noise filter, which pulls each pair
  toward the factor structure. These are not interchangeable: on a 27-name
  panel the banks sat at 0.71–0.76 raw and 0.65–0.69 cleaned, so a 0.70 bar on
  cleaned values quietly behaves like a 0.75 bar. The pipeline defaults to
  `raw`, because "correlated above 0.70" conventionally means the sample
  correlation.
- **mode: proportional / flat** — proportional takes nothing away at the
  threshold and the full factor at 1.0, so a name just over the line is barely
  touched. Flat applies the whole factor the moment the line is crossed.

The controls start where the scan was actually run (from `params` in
`sized_candidates.json`). Move them and the numbers turn amber, because a
simulated figure should not look like the pipeline's own; **reset to scan**
puts them back.

Held candidates are shown but never correlation-cut — owning more of a
position you already hold is the thing the modifier describes, not something
to describe about itself.

### ratings (dropdown)

- **candidates** — ticker (with a `held` tag), score and bar, liq, trend,
  pattern, sent, `guide → adjusted`, cut. Sortable and filterable; selecting a
  row drives ticker detail and the shared selection. The flag columns show the
  same words and glyphs as the terminal report, because they come from the
  same source: `liq` from `liquidity_flag`, `trend` from `roa_trend`,
  `pattern` from the conviction verdict (the divergence pattern crossed with
  sentiment), `sent` from `sentiment.json`.
- **changes since last scan** — what moved against the previous history
  snapshot, as `new` / `up` / `down` / `dropped` rows with the score before and
  after, the delta, and a reason read from a field that actually changed:
  "pattern is now disconnect", "trend now failing", "failed the liquidity
  filter", "fell below the minimum composite". Filter chips carry their counts.
  A name whose composite moved by less than 0.05 is not a row — that is inside
  the composite's own resolution and a table of forty such names is a table
  nobody reads. With no earlier snapshot it says so in a sentence.
- **popular & movers** — Phase 3.
- **holdings & health** — your positions with a monthly health read (below).

### correlation (dropdown, web view)

- **correlation heatmap** — candidates and holdings in one matrix, ordered so
  cluster members sit together and show up as blocks on the diagonal, with
  holdings last behind a dotted divider. Cells at or above the threshold get
  an amber outline that moves with the slider; cluster blocks get a cream
  frame labelled `pick_winner` or `industry_wide`. Hovering gives the pair and
  both the cleaned and raw rho. Negative correlations clamp to the low end of
  the colour ramp but keep their true sign in the tooltip.
- **score vs correlation** — composite against the largest correlation to any
  holding, with a dashed amber line at the current threshold. Points are split
  into full-size, reduced and held.
- **clusters** — a card per cluster: resolution, members with the winner
  marked, average correlation, dispersion and the sentence explaining the
  verdict. `industry_wide` clusters carry the sector-fund suggestion the
  pipeline already produces. Standalone names are listed with their reasons.

The heatmap's matrix **is not stored anywhere** — `sized_candidates.json` only
keeps candidate-to-holding pairs, and `clustered.json` only per-cluster
averages — so it is computed in a worker thread from cached prices, using the
same universe, lookback and period the sizing panel used, and cached in
`data/corr_matrix.json` keyed on the ticker set plus the scan's `generated_at`.
It then **checks itself**: every candidate/holding pair it recomputes must
match the stored value to within 0.01 on both bases. If any pair disagrees the
panel says so in amber and names the worst one, because numbers that quietly
contradict the sizing beside them are worse than a panel admitting it cannot
be trusted.

### sector ratings

One row per sector, best average first: the average on the chosen basis, how
many names, how many of them you hold, and the best ticker. Clicking a sector
filters the candidates table to it, switches the ratings dropdown to
**candidates** so you can see the filter you just applied, and shows a
`filtered: <sector>` chip; clicking the same sector again clears it. This panel
needs no new data — it is an aggregate of what the scan already wrote.

The basis toggle is **built from the data**, not hardcoded. The brief asks for
"composite / valuation / quality" and adds: check what keys actually exist and
only offer what the data supports. Checking is the operative word — the
evaluator writes exactly six dimensions (Valuation, Profitability, Growth,
Health, Momentum, Frameworks) and there is **no "quality"**. The nearest things
to it are Profitability and Health, which measure different claims; collapsing
them into one invented number would be this app asserting something the
evaluator does not. So the toggle offers composite plus whichever of the six
are actually present, discovered per scan. A scoring change that adds or
renames a dimension shows up here on its own.

Averages skip names with nothing to average rather than scoring them zero, and
each row's tooltip says how many of its names were measured.

### ticker detail

Always exactly one ticker, the subject, which arrives one of two ways.

**By selection** — a row in the ratings table, changes, or holdings. The source
line reads "selected in candidates".

**By lookup** — the box at the top takes any symbol. A name already in the scan
appears immediately with no network at all, and the source line reads "in
pipeline — scored 2h ago". Anything else is scored on demand through
`score_candidate`, the same single-ticker path the evaluator's batch mode uses,
off the UI thread with a visible progress state; the source line then reads
"looked up — evaluated just now". A bare Canadian symbol is tried against
`.TO`, `.V`, `.CN` and `.NE`, and if several listings return price history you
are **asked which one** rather than having one picked for you — a bare "SHOP"
is the US line or Toronto, and the two are different companies' worth of
different. An unknown symbol gets a plain sentence. A looked-up ticker that is
not in the scan has no sizing or correlation data, and those lines say "not in
the current scan" instead of computing something. Recent subjects sit in a chip
row under the box, kept in `settings.json`.

Left: name, sector, currency, venue, composite, the dimension bars, risk
flags, and the sized guidance with the holding it correlates with most. The
dimension bars use the evaluator's real keys — Valuation, Profitability,
Growth, Health, Momentum, Frameworks — read from the data rather than
hardcoded, so a scoring change does not leave the panel showing a stale set.

Right: Piotroski, Altman Z, ROA trend, FCF-positive years and the divergence
pattern (not the composite again — that is in the header), a 24-week price
sparkline, and a free-text note with a revisit date. Notes save on a short
debounce to `data/notes.json`, keyed by ticker, and work for any ticker.

### holdings & health

Columns: ticker (with its name, venue and currency), weight, cost basis, the
score a month ago against the score now, status, the next report date, and why.

Status comes from the section 10 thresholds, applied against the history
snapshot closest to `health_lookback_days` (30) ago:

| Status | Means |
|---|---|
| `review` | composite down `health_review_drop` (1.5) or more, or the trend turned failing, or debt turned rising, or the composite is now below `health_red_below` (5.0) |
| `watch` | composite down `health_watch_drop` (0.7) to 1.5, or it crossed down out of green |
| `intact` | none of the above fired |
| `building history` | less than `health_min_history_days` (14) of history, so there is nothing to compare against |
| `no data` | the evaluator could not score this holding |

These are triage flags, not sell signals. The status tooltip gives the exact
rule that fired, the whole rule set, and the pipeline's own verdict beside it.

`no data` exists because the alternative was wrong: a holding the evaluator
cannot score has no composite, so no rule can fire, and falling through to
`intact` would claim the thesis still holds on no evidence at all. The
evaluator takes the same care — `build_scores()` suppresses a composite below
its coverage minimum rather than reporting the name as mediocre.

**re-score** runs every holding through stage 1 again off the UI thread and
caches the result to `data/holdings_scored.json`, so the column is not blank
after a restart. It also fetches each holding's next report date.

#### Two verdicts, and why both are here

`position_sizer` already re-scores every holding on each run and writes its own
verdict into `sized_candidates.json` as `holdings_review`. This view does not
throw that away, because the two answer different questions:

- **the pipeline's** compares against the most recent archived run — "has this
  changed since the last scan"
- **this view's** compares against a month ago — "has this drifted over a month"

A holding losing 0.3 a week never trips the pipeline's 1.5-point drop between
two consecutive runs, and is down 1.5 here. The status column is the monthly
read, because that is what the panel is for; the pipeline's verdict is in the
tooltip, named as such.

The green threshold defaults to the evaluator's `COLOUR_GOOD` (6.25) rather
than the brief's 7.5, which predates the v5.5 rescale and would call almost
everything "crossed out of green". It stays overridable through
`health_green_from`.

### portfolio fit (web view)

Stacked bars by sector: what you hold now, and what the selected candidates
would add at their **adjusted midpoints**. Below it, a currency bar labelled
*mixed currency, not converted*.

Nothing here sums money across currencies. A holding's weight is its share of
cost basis **within its own currency**, and a mixed CAD/USD portfolio is
flagged rather than added up.

### calendar (dropdown)

Phase 3. The earnings-date lookup behind holdings & health is already built
(`jos/earnings.py`), so the two-week strip is a view over data the app can
already fetch.

---

## Exchanges, and when it is TSX only

Yahoo's Canadian suffixes mean different venues, and the app never collapses
them into "Canada":

| Suffix | Venue |
|---|---|
| `.TO` | TSX |
| `.V` | TSX Venture |
| `.CN` | CSE |
| `.NE` | Cboe Canada — where the CDRs trade, the CAD-hedged receipts over US megacaps |
| *(none)* | US |

The coverage line in the self-test and the status-chip tooltip is **derived
from the symbols actually present**, not from the scan's flags: `--include-canada`
says what was asked for, the suffixes say what arrived. So a scan that only
returned Toronto names reads `TSX only`, never `Canada`. You will see
`US + TSX`, `TSX only`, `TSX + TSXV, no US`, and so on.

The scan is run with `--include-canada` by default, from `scan_args` in
`data/settings.json`. Note that `universe_screen` already turns Canada on by
itself when `STOCKS_ACCOUNT_CURRENCY=CAD`, so passing it is belt and braces.

---

## Auto-update and autostart

```
powershell -ExecutionPolicy Bypass -File install_autostart.ps1
```

That puts **one shortcut in your Startup folder**, targeting
`pythonw.exe jostocks_autorun.py --reason logon`. No admin rights, nothing
installed, nothing outside your own Startup folder touched. It matches how
Dionysus already starts, so the two behave the same way on this machine.

`-ExecutionPolicy Bypass` matters: Windows refuses unsigned `.ps1` files by
default, and it refuses quietly enough to look like a broken script. It
applies to that one invocation and changes no setting.

### At logon

`jostocks_autorun.py` runs, in order:

1. **Takes a refresh lock** (a file with the PID and a timestamp). If a
   refresh is already running it exits — two scans at once would double the
   traffic Yahoo sees from one client, and the pipeline's backoff policy
   assumes one. A lock whose process has died, or one older than two hours, is
   treated as stale and taken over, so a killed autorun cannot wedge it.
2. **Waits for the network.** A logon fires before Wi-Fi is back, so
   connectivity is probed with backoff for up to `network_wait_minutes`,
   writing `waiting_for_network`. If it never appears the run finishes as
   `failed` with a reason and **the existing data is left alone**.
3. **Skips the scan** if the data is newer than `min_minutes_between_scans`
   (default 60). The refresh button ignores that floor.
4. **Runs `scan_report.py`** as a subprocess at below-normal priority with no
   console window, with `scan_args` and `--quiet` and nothing invented. It
   already skips stages whose output is fresh, re-runs everything downstream
   of a stage that ran, and enforces `market_data`'s caching and backoff —
   none of that is bypassed, and no full refresh is forced.
5. **Files a history snapshot**, fail-soft.
6. **Writes `data/status.json`** at every transition, which is what the
   toolbar chip reads.

Then it opens the window, or — if one is already open — tells it to reload
rather than starting a second copy.

### On wake — read this bit

**Wake is detected inside the app**, by noticing that the wall clock jumped
between one-second timer ticks (timers do not run while the machine is
suspended). That is the same trick Dionysus uses, and it catches hibernate and
a corrected system clock for free. There is **no scheduled task**.

The consequence, stated plainly: **with the jostocks window closed, waking the
machine does not refresh anything.** The next logon does. If you want a
refresh after sleep while the app is closed, that needs a Task Scheduler
"on an event" trigger (Log `System`, Source `Microsoft-Windows-Power-Troubleshooter`,
Event ID 1), which this build deliberately does not install.

### The status chip

| Chip | Means |
|---|---|
| `up to date` | the data is current |
| `refreshing: stage 4 of 7 (cluster)` | a scan is running; stage names and the count come from the pipeline's own `run_status.json` |
| `waiting for network` | woken or logged on before the network came back |
| `refresh failed, showing data from Oct 5` | the refresh failed; what is on screen is the last good scan |
| `scan halted at evaluate` | the scan itself stopped on a required stage, so the files below it are from an earlier run |
| `data 4d ago` | older than `stale_warning_days` |

Never a modal dialog at startup. The detail is in the tooltip.

### Settings

`data/settings.json`, created on first run:

```json
{
  "auto_refresh_on_logon": true,
  "auto_refresh_on_wake": true,
  "open_window_on_logon": true,
  "open_window_on_wake": false,
  "min_minutes_between_scans": 60,
  "network_wait_minutes": 5,
  "stale_warning_days": 3,
  "scan_enabled": true,
  "scan_args": ["--include-canada"],
  "pipeline_dir": null,
  "data_dir": null
}
```

`scan_enabled: false` makes a machine **view-only**: it reads the data and
watches it for changes but never runs the pipeline. That is the recommended
setting for a second machine on a synced data folder — the PC scans, the other
one only looks (see [Two machines](#two-machines)).

### Uninstall

```
powershell -ExecutionPolicy Bypass -File uninstall_autostart.ps1
```

Removes the Startup shortcut (and any scheduled task an earlier build left
behind). Your settings, notes, history and the pipeline's data are untouched,
and `launch.bat` still works.

---

## Where state lives

Everything the app owns is under `data/`, written atomically through the
pipeline's own `write_json` — which retries `os.replace` on `PermissionError`,
because a Windows virus scanner or OneDrive holds a newly created file for a
few milliseconds and a note saved on every keystroke would otherwise be lost.

| Path | What |
|---|---|
| `data/settings.json` | window geometry, remembered dropdown views, slider positions, the settings above. **Per machine** — never put this in a synced folder |
| `data/notes.json` | `{ticker: {text, revisit, saved_at}}` |
| `data/history/` | dated copies of `sized_candidates.json` (`YYYY-MM-DD_HHMM.json`), taken only when a scan produced new data, pruned past 90 days |
| `data/corr_matrix.json` | the cached correlation matrix, keyed on the ticker set plus the scan's `generated_at` |
| `data/holdings_scored.json` | the re-scored holdings, so the health column survives a restart |
| `data/earnings_cache.json` | next report dates, a week's TTL, `unknown` where Yahoo has none |
| `data/status.json` | what the refresh is doing; written by the autorun, read by the chip |
| `data/refresh.lock` | the one-scan-at-a-time lock |
| `data/logs/autorun.log` | rotating; under `pythonw` there is no console, so this is the only record of what a logon run did |

The pipeline's own `data/archive/<timestamp>/` already holds a full copy of
each completed run (30 by default). The app keeps its own history because it
is bounded in days rather than runs, but the archive reaches further back if
you need it.

---

## Keeping in step with the pipeline

The app imports the pipeline rather than copying it: `position_sizer` for the
sizing, `rmt_cluster` for the correlation matrix, `stock_evaluator` for
scoring and the colour thresholds, `scan_report` for the sector-fund
suggestion, `market_data` for every network call. Nothing here calls yfinance
directly.

Two guards exist because "imported" is not the same as "in agreement":

- **The sizing self-check.** On every load the app re-derives each candidate's
  sizing using the scan's own recorded parameters, and the result must match
  the `scale` already on disk. A mismatch means `position_sizer` changed and
  `jos/sizing.py` has to follow; it shows as a warning on the freshness text
  and in `--self-test`.
- **The correlation self-check**, described under the heatmap above.

Three things the app reads from the pipeline rather than hardcoding, each
because the obvious literal is already out of date:

- **Score colours** are `stock_evaluator.COLOUR_GOOD` / `COLOUR_FAIR`, which
  are **6.25 and 5.00** — not 7.5. Evaluator v5.5 rescaled the composite
  (`score()` maps `good` to 9.0, not 10.0) and the rating bands moved with it.
- **The dimension keys** are read from `dims`, and there are six.
- **The stage count** comes from `run_status.json`. The pipeline has seven
  stages, three of them optional.

### The conviction modifier

Worth knowing if you ever reimplement the sizing: a candidate's total scale is
**`correlation_scale × conviction_scale`**, not the correlation part alone.
The conviction modifier is the divergence pattern crossed with public
sentiment, and it ranges from 1.00 down to 0.50 — a `price_disconnect` nobody
is talking about and one the public is actively negative on are different
situations and size differently.

Leaving it out is not a rounding error. On the test fixture, `BNS.TO` sizes to
a scale of **0.423** in the pipeline; the correlation part alone gives
**0.651**, a position **54% larger** than the pipeline says. `jos/sizing.py`
reads the stored `conviction_scale` back and multiplies it in, exactly as
`size_candidate`'s `finalize()` does.

### Holdings health: the Phase 1 caveat, resolved

Phase 1 flagged that `position_sizer` already re-scores every holding and
wondered whether the brief's health module was a second opinion on a settled
question. Reading the source settles it: it is not. The pipeline's baseline is
`latest_archive_scores()` — the most recent archived run — so it reports
run-to-run change. The brief's baseline is a month ago. A holding sliding 0.3
a week never trips the pipeline's 1.5-point drop between consecutive runs and
is down 1.5 over a month, so the two genuinely disagree on the names that
matter most. Both are shown; see
[holdings & health](#holdings--health) above.

---

## Two machines

The pipeline data, holdings, notes and history live in one data directory.
Each machine has its own copy unless you point `STOCKS_DATA_DIR` at a synced
folder (iCloud Drive, OneDrive, Dropbox).

- **Do not let two machines scan at once.** Set `scan_enabled: false` on the
  second one. Recommended: the PC scans, the other machine is view-only on a
  synced data folder.
- Keep `settings.json` **per machine** — it holds window geometry and view
  choices.
- Sync tools deliver half-written and conflicted files. The app reads every
  JSON file defensively: a read error keeps the last good data and retries on
  the next change event, rather than blanking a panel.

macOS autostart is Phase 4. The platform layer it needs is already built —
`platform_support.py` holds `install_autostart()`, `uninstall_autostart()`,
`lower_priority()` and `network_ready()`, and nothing Windows-specific lives
anywhere else — so it is the installer scripts and a LaunchAgent that remain,
not a redesign.

---

## Fonts

Fraunces (panel titles), Inter (body) and JetBrains Mono (numbers). Drop the
`.ttf` files into `fonts/` and they load at startup with
`QFontDatabase.addApplicationFont`, so the window looks the same on a machine
that has none of them installed. Without them it falls back to the nearest
serif, sans and monospace the machine has, and nothing breaks.

Both free on Google Fonts (OFL):
<https://fonts.google.com/specimen/Fraunces> ·
<https://fonts.google.com/specimen/Inter> ·
<https://fonts.google.com/specimen/JetBrains+Mono>

---

## If something is wrong

| Symptom | Cause |
|---|---|
| "could not find market_data.py" | jostocks is not beside the pipeline. Set `pipeline_dir` in `data/settings.json`, or `STOCKS_DIR`. |
| Chart panels say they need PyQt6-WebEngine | `pip install PyQt6-WebEngine`. It is a separate wheel from PyQt6; everything else in the window works without it. |
| "no data yet, scan running or not run" | nothing has written `sized_candidates.json`. Run `run_scan.bat`, or press refresh. |
| The window never appears at logon | `data/logs/autorun.log` is the record. Under `pythonw` there is no console for a traceback to land in. |
| Scores look lower than you remember | evaluator v5.5 rescaled the composite; green starts at 6.25 now. |
| Changes since last scan says there is nothing to compare | only one scan has been snapshotted so far. It fills in after the next one. |
| Every holding reads "building history" | `data/history/` is less than `health_min_history_days` deep. It resolves itself as snapshots accumulate. |
| A holding reads "no data" | the evaluator returned no composite for it — usually it has left the scanned universe. Press re-score; it is never guessed at. |
| A next report date is "unknown" | Yahoo has no date for that name. Section 10 decided never to guess one. |

```
python jostocks.py --self-test
```

is the first thing to run, in any of those cases.

### Checking it yourself

`tests/` holds what was used to verify this build, and it runs on your machine
too:

```
python tests/make_fixtures.py tests/fakedata   # fixtures, built through the real position_sizer
python tests/make_history.py tests/fakedata    # backdated snapshots to compare against
python tests/logic_tests.py                    # locks, freshness, venues, history, no-data paths
python tests/drive_app.py                      # opens the window and exercises the interactions
python tests/phase2_tests.py                   # sectors, changes, lookup, holdings health
```

`make_fixtures.py` builds a data directory by calling the pipeline's own
`size_candidate` and `resolve_cluster`, so the fixtures cannot drift from the
real schemas. `make_history.py` then writes backdated copies with drifted
scores, which is what gives the changes view and the 30-day health comparison
something real to work against — run it before `phase2_tests.py`. On Windows,
drop `QT_QPA_PLATFORM=offscreen` to watch the window do it.

The fixtures deliberately include awkward cases, because those are the ones
that break things: a candidate whose only stored correlation is `NaN`, a
holding the evaluator cannot score, a name that left the universe, and a
holding that slides slowly enough to escape the pipeline's own exit review.

---

## What is in this phase

**Phase 1.** Shell, theme, platform layer, data loading and watcher, sizing
strip, candidates table, the correlation dropdown (heatmap, scatter,
clusters), ticker detail, portfolio fit, holdings, the status chip,
`jostocks_autorun.py`, the installers, the launcher, this README. Notes and
history snapshots came in early too — notes because ticker detail wanted
somewhere to put them, history because the autorun needed to file one.

**Phase 2 (this build).** Changes since last scan, sector ratings, the lookup
box in ticker detail, and holdings health with its 30-day comparison. The
earnings-date lookup the health column needs is built as well, behind the
small interface section 10 asks for.

**Phase 3.** Popular & movers, the two-week earnings strip, macro releases.

**Phase 4.** macOS autostart and view-only mode.

### Not done, and not planned

- Does not modify `sized_candidates.json`, `clustered.json`,
  `scored_candidates.json` or `holdings.json`.
- Does not replace or integrate with Dionysus.
- No AI/LLM features, news feeds, or anything that starts Ollama — nothing in
  this app is GPU-bound.
- No aggregation of market values across currencies without conversion.
