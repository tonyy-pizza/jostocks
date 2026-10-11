r"""jos/earnings.py - when each name next reports.

Decided in section 10: yfinance, through market_data, and where it has no
date the answer is "unknown" - never a guess. Kept behind one small interface
(`next_report`, `next_reports`) so a second source can be added later without
anything else changing.

WHY THIS DOES NOT CALL yfinance DIRECTLY, AND DOES NOT EDIT market_data
-----------------------------------------------------------------------
Section 3 says every yfinance call goes through market_data, and that a new
fetch should be added to market_data following its caching, backoff and
ticker-identity conventions. market_data has no earnings function, and this
project cannot add one: the pipeline is a separate repository that jostocks
reads and never writes.

So this builds on market_data's own primitives rather than around them -
`get_ticker` for the shared impersonating session, `fetch_with_backoff` for
the retry policy, `cached_fetch` for the on-disk cache under cache\financials\
with the statements TTL. Yahoo still sees one well-behaved client, the policies
stay in one place, and nothing here constructs a yf.Ticker or an HTTP session
of its own.

`prefer_pipeline()` checks at runtime for a native market_data earnings
function and uses it if one ever appears, so adding it upstream makes this
defer rather than compete.

A second, app-owned cache in data/earnings_cache.json is kept on top. That is
not redundant: market_data's cache lives beside the pipeline and is keyed for
its own purposes, while section 8 names earnings_cache.json as state this app
owns, and it is what lets a holdings table draw a date with no network at all
on a machine running view-only.
"""

from __future__ import annotations

import datetime as dt

from . import pipeline as pipe
from .paths import EARNINGS_CACHE_PATH
from .state import read_json, write_json

#: Earnings dates move, but not hourly. A week matches market_data's
#: TTL_FINANCIALS, which is the cache this shares.
TTL_SECONDS = 7 * 24 * 60 * 60

#: What a row shows when there is no date. Section 10: never guess.
UNKNOWN = "unknown"


def prefer_pipeline():
    """A native market_data earnings function, if one is ever added."""
    if not pipe.ready() or pipe.md is None:
        return None
    for name in ("get_earnings_date", "get_next_earnings", "earnings_date"):
        function = getattr(pipe.md, name, None)
        if callable(function):
            return function
    return None


def _as_date(value):
    """Coerce whatever yfinance hands back into a date, or None.

    It returns datetimes, dates, pandas Timestamps, strings and NaT depending
    on the field and the version, and NaT in particular compares oddly, so
    everything funnels through here.
    """
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value

    # pandas Timestamp and NaT
    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
        try:
            if value != value:                  # NaT
                return None
            return to_pydatetime().date()
        except Exception:                       # noqa: BLE001
            return None

    text = str(value).strip()
    if not text or text.lower() in ("nat", "none", "nan"):
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _pull(symbol):
    """One ticker's next report date and timing, straight from Yahoo.

    Two sources, because neither is reliable alone: `calendar` carries the
    confirmed-or-estimated next date for most names, and `earnings_dates`
    carries a table that usually includes the upcoming row and sometimes a
    timing hint. The earliest date not in the past wins.
    """
    ticker = pipe.md.get_ticker(symbol)

    found, timing = [], None

    try:
        calendar = ticker.calendar
    except Exception:                           # noqa: BLE001
        calendar = None

    if isinstance(calendar, dict):
        for key in ("Earnings Date", "earningsDate"):
            value = calendar.get(key)
            if isinstance(value, (list, tuple)):
                found.extend(_as_date(v) for v in value)
            else:
                found.append(_as_date(value))
    elif calendar is not None:
        # Older yfinance returned a DataFrame keyed by row label.
        try:
            for key in ("Earnings Date", "earningsDate"):
                if key in getattr(calendar, "index", ()):
                    row = calendar.loc[key]
                    values = row.tolist() if hasattr(row, "tolist") else [row]
                    found.extend(_as_date(v) for v in values)
        except Exception:                       # noqa: BLE001
            pass

    try:
        table = ticker.earnings_dates
    except Exception:                           # noqa: BLE001
        table = None

    if table is not None and getattr(table, "empty", True) is False:
        try:
            today = dt.date.today()
            for stamp in list(table.index):
                when = _as_date(stamp)
                if when and when >= today:
                    found.append(when)
            column = next((c for c in table.columns
                           if "time" in str(c).lower()), None)
            if column is not None:
                for stamp in list(table.index):
                    when = _as_date(stamp)
                    if when and when >= dt.date.today():
                        hint = str(table.loc[stamp, column]).strip()
                        if hint and hint.lower() not in ("nan", "none"):
                            timing = hint
                        break
        except Exception:                       # noqa: BLE001
            pass

    today = dt.date.today()
    upcoming = sorted({d for d in found if d is not None and d >= today})
    if not upcoming:
        return {"date": None, "timing": None}
    return {"date": upcoming[0].isoformat(), "timing": timing}


def _cache() -> dict:
    document = read_json(EARNINGS_CACHE_PATH)
    return document if isinstance(document, dict) else {}


def _cache_fresh(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    stamp = entry.get("fetched_at")
    if not stamp:
        return False
    try:
        fetched = dt.datetime.fromisoformat(str(stamp)[:19])
    except ValueError:
        return False
    return (dt.datetime.now() - fetched).total_seconds() < TTL_SECONDS


def next_reports(tickers, force_refresh=False, progress=None) -> dict:
    """{ticker: {"date": "YYYY-MM-DD"|None, "timing": str|None}}.

    Runs in a worker thread - it is network-bound. Never raises: a symbol
    Yahoo has nothing for comes back with a None date, which the table shows
    as "unknown".
    """
    symbols = [str(t).strip().upper() for t in (tickers or ()) if str(t).strip()]
    if not symbols:
        return {}

    cached = _cache()
    out, to_fetch = {}, []

    for symbol in symbols:
        entry = cached.get(symbol)
        if not force_refresh and _cache_fresh(entry):
            out[symbol] = {"date": entry.get("date"),
                           "timing": entry.get("timing")}
        else:
            to_fetch.append(symbol)

    if not to_fetch:
        return out

    pipe.require()
    native = prefer_pipeline()

    for index, symbol in enumerate(to_fetch, start=1):
        if progress:
            progress(f"earnings dates: {symbol} ({index} of {len(to_fetch)})...")
        try:
            if native is not None:
                result = {"date": _as_date(native(symbol)), "timing": None}
                if result["date"] is not None:
                    result["date"] = result["date"].isoformat()
            else:
                # market_data's cache, TTL and backoff; no session of our own.
                result = pipe.md.cached_fetch(
                    f"earnings_next_{symbol}",
                    lambda s=symbol: pipe.md.fetch_with_backoff(_pull, s),
                    TTL_SECONDS,
                    cache_type="financials",
                    force_refresh=force_refresh)
        except Exception:                       # noqa: BLE001
            result = None

        if not isinstance(result, dict):
            result = {"date": None, "timing": None}

        out[symbol] = {"date": result.get("date"),
                       "timing": result.get("timing")}
        cached[symbol] = {**out[symbol],
                          "fetched_at": dt.datetime.now().isoformat(
                              timespec="seconds")}

    write_json(cached, EARNINGS_CACHE_PATH)
    return out


def next_report(ticker, force_refresh=False):
    """One ticker's next report date, or None."""
    return next_reports([ticker], force_refresh=force_refresh).get(
        str(ticker).strip().upper(), {"date": None, "timing": None})


def describe(entry) -> str:
    """"Nov 4 (after close), in 12 days", or "unknown"."""
    if not isinstance(entry, dict) or not entry.get("date"):
        return UNKNOWN
    try:
        when = dt.date.fromisoformat(str(entry["date"])[:10])
    except ValueError:
        return UNKNOWN

    text = f"{when:%b} {when.day}"
    timing = str(entry.get("timing") or "").lower()
    if "before" in timing or "bmo" in timing:
        text += " (before open)"
    elif "after" in timing or "amc" in timing:
        text += " (after close)"

    days = (when - dt.date.today()).days
    if days == 0:
        return f"{text}, today"
    if days == 1:
        return f"{text}, tomorrow"
    if days > 1:
        return f"{text}, in {days} days"
    return text
