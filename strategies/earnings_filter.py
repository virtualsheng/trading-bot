"""
earnings_filter.py — Earnings Calendar Filter
───────────────────────────────────────────────
Prevents new position entries within 48 hours of an earnings report.

Why this matters: earnings announcements cause overnight gaps that can
blow through stop losses on leveraged ETFs. A 3x ETF on a stock that
gaps -10% on earnings = -30% overnight, far beyond any intraday stop.

Data source: Yahoo Finance earnings calendar (free, no API key needed).
Falls back to allowing the trade if the calendar can't be fetched.

v2 fix: "No earnings dates found" logged at DEBUG instead of ERROR.
  ETFs and most macro/commodity symbols never have earnings — logging
  this at ERROR level was noise. Changed to debug so it only appears
  when VERBOSE logging is enabled.
"""

import datetime
import logging
import pytz
from typing import Optional

logger = logging.getLogger(__name__)

# Cache earnings dates per symbol per day to avoid repeated yfinance calls
# Format: {"NVDA": {"fetched": date, "next_earnings": date_or_None}}
_earnings_cache: dict = {}

# Use ET timezone consistently so cache date matches trading day
_ET = pytz.timezone("US/Eastern")


def _today_et() -> datetime.date:
    """Return today's date in US/Eastern time."""
    return datetime.datetime.now(_ET).date()


def _get_next_earnings_date(symbol: str) -> Optional[datetime.date]:
    """
    Fetch the next earnings date for a symbol from Yahoo Finance.
    Returns None if unavailable or if the symbol has no upcoming earnings.

    Logs at DEBUG level (not ERROR) when no earnings are found — ETFs and
    commodity/macro symbols legitimately have no earnings schedule.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)

        cal = ticker.earnings_dates
        if cal is None or cal.empty:
            # ETFs, commodities, leveraged products — expected, not an error
            logger.debug(f"{symbol}: No earnings dates found (ETF or no earnings schedule)")
            return None

        today = _today_et()
        future = cal[cal.index.date >= today]  # type: ignore
        if future.empty:
            logger.debug(f"{symbol}: No future earnings dates found")
            return None

        # The nearest upcoming earnings date
        next_date = future.index[-1].date()  # last = earliest future
        return next_date

    except Exception as e:
        logger.debug(f"{symbol}: Earnings calendar fetch failed ({e}) — allowing trade")
        return None


def is_earnings_safe(
    symbol: str,
    buffer_hours: int = 48,
    use_cache: bool = True,
) -> bool:
    """
    Returns True if it is safe to open a new position (no earnings within buffer_hours).
    Returns False if an earnings report is scheduled within the next buffer_hours.

    Fails OPEN (returns True) if the earnings calendar cannot be fetched,
    so a network issue never silently blocks all trades.

    Parameters
    ----------
    symbol       : ticker to check
    buffer_hours : hours before earnings to block new entries (default 48)
    use_cache    : use cached result if fetched today (avoids repeated yfinance calls)
    """
    today = _today_et()

    # Check cache
    if use_cache and symbol in _earnings_cache:
        cached = _earnings_cache[symbol]
        if cached["fetched"] == today:
            next_earnings = cached["next_earnings"]
            if next_earnings is None:
                return True
            now_et        = datetime.datetime.now(_ET).replace(tzinfo=None)
            earnings_dt   = datetime.datetime.combine(next_earnings, datetime.time(16, 0))
            delta         = earnings_dt - now_et
            return delta.total_seconds() > buffer_hours * 3600

    # Fetch fresh
    next_earnings = _get_next_earnings_date(symbol)

    # Store in cache
    _earnings_cache[symbol] = {
        "fetched":       today,
        "next_earnings": next_earnings,
    }

    if next_earnings is None:
        return True  # No earnings data = safe to trade

    now_et      = datetime.datetime.now(_ET).replace(tzinfo=None)
    earnings_dt = datetime.datetime.combine(next_earnings, datetime.time(16, 0))
    hours_until = (earnings_dt - now_et).total_seconds() / 3600

    if hours_until < 0:
        # Earnings was today/already passed — check if within 4 hours after
        return abs(hours_until) > 4

    return hours_until > buffer_hours


def get_earnings_info(symbol: str) -> dict:
    """Return earnings calendar info for a symbol (for logging/debugging)."""
    next_earnings = _get_next_earnings_date(symbol)

    if next_earnings is None:
        return {"symbol": symbol, "next_earnings": None, "hours_until": None, "safe": True}

    now_et      = datetime.datetime.now(_ET).replace(tzinfo=None)
    earnings_dt = datetime.datetime.combine(next_earnings, datetime.time(16, 0))
    hours_until = (earnings_dt - now_et).total_seconds() / 3600

    return {
        "symbol":        symbol,
        "next_earnings": str(next_earnings),
        "hours_until":   round(hours_until, 1),
        "safe":          hours_until > 48,
    }


def clear_cache():
    """Clear the earnings cache — call at start of each trading day."""
    global _earnings_cache
    _earnings_cache = {}


def prefetch_earnings(symbols: list):
    """
    Pre-warm the earnings cache for all symbols at startup.
    Fires _get_next_earnings_date() for each symbol so the results are
    cached before the first trade check, avoiding per-symbol fetch latency
    during the ORB window.

    Suppresses stdout noise from yfinance during fetch — only prints a
    clean summary line at the end.
    """
    import sys, io
    upcoming = []
    for symbol in symbols:
        try:
            _stdout   = sys.stdout
            sys.stdout = io.StringIO()
            date      = _get_next_earnings_date(symbol)
            sys.stdout = _stdout
            # Cache the result
            _earnings_cache[symbol] = {
                "fetched":       _today_et(),
                "next_earnings": date,
            }
            if date:
                upcoming.append(f"{symbol}:{date}")
        except Exception:
            try:
                sys.stdout = _stdout  # type: ignore
            except Exception:
                pass

    if upcoming:
        print(f"[startup] Earnings alerts: {', '.join(upcoming)}")
    else:
        print("[startup] Earnings cache ready — no imminent reports found")