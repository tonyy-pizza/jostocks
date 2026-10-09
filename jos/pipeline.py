r"""jos/pipeline.py - find the scan pipeline and import it, once.

Every pipeline module the app uses is imported here and re-exported, so the
rest of jostocks says `from jos import pipeline as pipe` and never repeats the
sys.path dance. Three reasons it lives in one place:

  - The search itself is the pipeline's own. stocks_common documents why each
    stage carries an inline copy (a script cannot call the helper until it can
    import the module the helper exists to find), and jostocks is in exactly
    that position: it sits in a sibling folder and must put the pipeline on
    sys.path before `import market_data` can work.
  - The pipeline is a hard dependency for sizing and correlations but not for
    drawing a window. An install where the pipeline is missing should open and
    say so, not traceback before the first paint - so the import is tolerant
    and the failure is a value (`IMPORT_ERROR`) the UI can render.
  - It is the single chokepoint for "never call yfinance directly". Nothing
    else in jostocks imports market_data.

Layout this expects (the brief's suggested one):

    C:\Users\joey\stocks\          market_data.py, position_sizer.py, ...
    C:\Users\joey\stocks\jostocks\ this project

so the pipeline is the parent directory. `settings.json`'s `pipeline_dir`
overrides the search for an install that is laid out differently.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# The file whose presence marks the pipeline folder. market_data.py is the one
# every other stage imports, so a folder holding it is the folder holding them.
MARKER = "market_data.py"

PROJECT_DIR = Path(__file__).resolve().parent.parent

#: Set by :func:`bootstrap` to the directory the pipeline was found in.
PIPELINE_DIR: Optional[Path] = None

#: ``"ModuleNotFoundError: ..."`` when the pipeline could not be imported,
#: else None. The UI shows this instead of candidate data.
IMPORT_ERROR: Optional[str] = None

# Re-exported pipeline modules. None until bootstrap() succeeds.
md = None           # market_data
common = None       # stocks_common
ps = None           # position_sizer
rc = None           # rmt_cluster
se = None           # stock_evaluator
sr = None           # scan_report

_READY = False


def candidate_dirs(override=None):
    """Where to look for the pipeline, best guess first.

    The middle entry mirrors stocks_common.add_project_dir_to_path(), which
    searches `here.parent / "stocks"` as well: the repository keeps the stages
    in a `stocks/` subfolder, so a checkout rather than the deployed layout
    puts them one level deeper than the parent.
    """
    if override:
        yield Path(override).expanduser()
    env = os.environ.get("STOCKS_DIR")
    if env:
        yield Path(env).expanduser()
    here = PROJECT_DIR
    yield here.parent
    yield here.parent / "stocks"
    yield here
    yield here / "stocks"


def find_pipeline_dir(override=None) -> Optional[Path]:
    """The first candidate directory that actually holds the pipeline."""
    for candidate in candidate_dirs(override):
        try:
            if (candidate / MARKER).is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def bootstrap(override=None, quiet=True) -> bool:
    """Put the pipeline on sys.path and import it. True when it worked.

    Safe to call repeatedly; the work happens once. A failure is recorded in
    IMPORT_ERROR and returns False rather than raising, because the window has
    to open either way - an app that cannot start is worse at telling you the
    pipeline is missing than an app that starts and says so.
    """
    global PIPELINE_DIR, IMPORT_ERROR, md, common, ps, rc, se, sr, _READY

    if _READY:
        return True

    directory = find_pipeline_dir(override)
    if directory is None:
        looked = ", ".join(str(p) for p in candidate_dirs(override))
        IMPORT_ERROR = (f"could not find {MARKER}. Looked in: {looked}. "
                        f"Set pipeline_dir in settings.json, or $STOCKS_DIR.")
        return False

    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

    try:
        import market_data as _md
        import stocks_common as _common
        import rmt_cluster as _rc
        import stock_evaluator as _se
        import position_sizer as _ps
        import scan_report as _sr
    except Exception as exc:                       # noqa: BLE001
        IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        if not quiet:
            print(f"  pipeline import failed: {IMPORT_ERROR}")
        return False

    md, common, ps, rc, se, sr = _md, _common, _ps, _rc, _se, _sr
    PIPELINE_DIR, IMPORT_ERROR, _READY = directory, None, True
    return True


def ready() -> bool:
    """Whether the pipeline is importable and imported."""
    return _READY


def require():
    """Bootstrap or raise. For worker threads, where a failure is an error."""
    if not bootstrap():
        raise RuntimeError(f"stocks pipeline unavailable: {IMPORT_ERROR}")


def scan_report_script() -> Optional[Path]:
    """Path to scan_report.py, for the autorun subprocess."""
    directory = PIPELINE_DIR or find_pipeline_dir()
    if directory is None:
        return None
    script = directory / "scan_report.py"
    return script if script.is_file() else None


# ─── thresholds and labels, taken from the pipeline rather than copied ────
# The brief quotes scan_report.colour() as green >= 7.5. It is not: the
# evaluator's own COLOUR_GOOD/COLOUR_FAIR are 6.25/5.00, because v5.5 rescaled
# the composite (score() maps `good` to 9.0, not 10.0) and the bands were
# scaled with it. Reading them from the module is the only way the app and the
# terminal report stay in agreement through the next recalibration.
FALLBACK_GOOD, FALLBACK_FAIR = 6.25, 5.00


def colour_thresholds():
    """(good, fair) composite cutoffs, from stock_evaluator."""
    if se is not None:
        good = getattr(se, "COLOUR_GOOD", None)
        fair = getattr(se, "COLOUR_FAIR", None)
        if isinstance(good, (int, float)) and isinstance(fair, (int, float)):
            return float(good), float(fair)
    return FALLBACK_GOOD, FALLBACK_FAIR


def rating_bands():
    """The four rating-band edges, from stock_evaluator."""
    if se is not None:
        bands = getattr(se, "RATING_BANDS", None)
        if isinstance(bands, (tuple, list)) and len(bands) == 4:
            return tuple(float(b) for b in bands)
    return (7.75, 6.25, 5.00, 3.75)


#: The dimension keys build_scores() actually writes, in its own order.
#: Hardcoded as a fallback only; dims_order() prefers whatever is on disk.
DIM_NAMES = ("Valuation", "Profitability", "Growth", "Health", "Momentum",
             "Frameworks")


def dims_order(dims) -> list:
    """The dimension keys of one candidate, in the evaluator's order.

    Keys the evaluator stopped writing drop out and ones it added appear at
    the end, so a scoring change does not need a change here. The brief says
    to use the real keys from `dims`, and this is what that means in practice.
    """
    if not isinstance(dims, dict):
        return []
    known = [n for n in DIM_NAMES if n in dims]
    extra = [k for k in dims if k not in DIM_NAMES]
    return known + sorted(extra)


def num(value):
    """stocks_common.num when the pipeline is up, else the same contract.

    Folding NaN to None is not a detail: it is the value that compares False
    against every threshold, so a NaN that survives reaches a "big enough to
    act on" test and passes it.
    """
    if common is not None:
        return common.num(value)
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out
