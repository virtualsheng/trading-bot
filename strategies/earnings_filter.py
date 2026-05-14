"""
earnings_filter.py — Earnings Calendar Filter
───────────────────────────────────────────────
Prevents new position entries within 48 hours of an earnings report.

Why this matters: earnings announcements cause overnight gaps that can
blow through stop losses on leveraged ETFs. A 3x ETF on a stock that
gaps -10% on earnings = -30% overnight, far beyond any intraday stop.

Data source: Yahoo Finance earnings calendar (free, no API key needed).
Falls back to allowing the trade if the calendar can't be fetched.

Usage:
    from strategies.earnings_filter import is_earnings_safe

    if not is_earnings_safe(symbol):
        return  # Skip — earnings within 48 hours
"""

import datetime
import functools
from typing import Optional

# Cache earnings dates per symbol per day to avoid repeated yfinance calls
# Format: {"NVDA": {"fetched": date, "next_earnings": date_or_None}}
_earnings_cache: dict = {}


def _get_next_earnings_date(symbol: str) -> Optional[datetime.date]:
    """
    Fetch the next earnings date for a symbol from Yahoo Finance.
    Returns None if unavailable or if the symbol has no upcoming earnings.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)

        # yfinance provides earnings dates in .earnings_dates
        # This is a DataFrame indexed by date — most recent first
        cal = ticker.earnings_dates
        if cal is None or cal.empty:
            return None

        today = datetime.date.today()
        # Filter to future dates only (including today)
        future = cal[cal.index.date >= today]  # type: ignore
        if future.empty:
            return None

        # The nearest upcoming earnings date
        next_date = future.index[-1].date()  # last = earliest future
        return next_date

    except Exception:
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
    today = datetime.date.today()

    # Check cache
    if use_cache and symbol in _earnings_cache:
        cached = _earnings_cache[symbol]
        if cached["fetched"] == today:
            next_earnings = cached["next_earnings"]
            if next_earnings is None:
                return True  # No upcoming earnings
            delta = datetime.datetime.combine(next_earnings, datetime.time(16, 0))
            delta = delta - datetime.datetime.now()
            return delta.total_seconds() > buffer_hours * 3600

    # Fetch fresh
    next_earnings = _get_next_earnings_date(symbol)

    # Store in cache
    _earnings_cache[symbol] = {
        "fetched":        today,
        "next_earnings":  next_earnings,
    }

    if next_earnings is None:
        return True  # No earnings data = safe to trade

    # Calculate hours until earnings (assume 4pm ET report time)
    earnings_dt = datetime.datetime.combine(next_earnings, datetime.time(16, 0))
    hours_until  = (earnings_dt - datetime.datetime.now()).total_seconds() / 3600

    if hours_until < 0:
        # Earnings was today/already passed — check if within 4 hours after
        # (sometimes earnings are announced pre-market the next morning)
        return abs(hours_until) > 4

    safe = hours_until > buffer_hours

    return safe


def get_earnings_info(symbol: str) -> dict:
    """
    Return earnings calendar info for a symbol.
    Useful for logging/debugging.
    """
    today = datetime.date.today()
    next_earnings = _get_next_earnings_date(symbol)

    if next_earnings is None:
        return {"symbol": symbol, "next_earnings": None, "hours_until": None, "safe": True}

    earnings_dt  = datetime.datetime.combine(next_earnings, datetime.time(16, 0))
    hours_until  = (earnings_dt - datetime.datetime.now()).total_seconds() / 3600

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

    yfinance prints "No earnings dates found" for every ETF (which is most
    of the watchlist). We suppress stdout during prefetch to avoid startup
    noise — the filter still works correctly regardless.
    """
    import sys, io
    upcoming = []
    for symbol in symbols:
        try:
            # Suppress yfinance's verbose ETF warnings during bulk prefetch
            _stdout = sys.stdout
            sys.stdout = io.StringIO()
            date = _get_next_earnings_date(symbol)
            sys.stdout = _stdout
            if date:
                upcoming.append(f"{symbol}:{date}")
        except Exception:
            try: sys.stdout = _stdout
            except: pass

    if upcoming:
        print(f"[startup] Earnings alerts: {', '.join(upcoming)}")
    else:
        print(f"[startup] Earnings cache ready — no imminent reports found")