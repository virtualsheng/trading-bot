"""
expected_move.py — Options-implied expected move for signal symbols
────────────────────────────────────────────────────────────────────
Calculates the market's own expected daily and weekly price range
for QQQ and SMH using the ATM straddle price from the options chain.

Formula:
    Expected Move = ATM Straddle × 0.68
    ATM Straddle  = ATM call mid-price + ATM put mid-price
    × 0.68 converts 1 standard deviation to the ~68% probability range

Execution ETF EM = Signal symbol EM × leverage multiple (3×):
    QQQ EM × 3 → TQQQ/SQQQ expected move
    SMH EM × 3 → SOXL/SOXS expected move

Used by the strategy to:
  - Validate stop distance (stop < 1/3 of daily EM = inside noise)
  - Log target vs EM boundary context
  - EOD signal report: show expected range for each signal symbol

Free — yfinance options chain, no API key required.
Cached per symbol per session.

Usage:
    from strategies.expected_move import get_expected_move, get_all_expected_moves
    em = get_expected_move("QQQ")
    ems = get_all_expected_moves()   # {symbol: em_dict}
"""

import json
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Per-symbol in-memory cache — refreshed once per session
_cache: dict = {}          # {symbol: result_dict}
_cache_date: str = ""

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "cache", "expected_move_cache.json")

# Signal symbols → execution tickers for EM calculation
SIGNAL_TO_EXEC = {
    "QQQ": {"bull": "TQQQ", "bear": "SQQQ", "leverage": 3},
    "SMH": {"bull": "SOXL", "bear": "SOXS", "leverage": 3},
}


def _load_disk_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        # Only use if cached today
        if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return data
    except Exception:
        pass
    return {}


def _save_disk_cache(data: dict):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _next_friday() -> str:
    today      = datetime.now()
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def _calc_straddle_em(ticker_obj, expiry: str, price: float) -> dict | None:
    """
    Calculate expected move for a specific expiry using ATM straddle.
    Returns None if options data unavailable.
    """
    try:
        chain = ticker_obj.option_chain(expiry)
        calls = chain.calls
        puts  = chain.puts

        if calls.empty or puts.empty:
            return None

        # Find ATM strike — closest to current price
        atm_idx    = (calls["strike"] - price).abs().argsort().iloc[0]
        atm_strike = float(calls.iloc[atm_idx]["strike"])

        atm_call_row = calls[calls["strike"] == atm_strike]
        atm_put_row  = puts[puts["strike"]   == atm_strike]

        if atm_call_row.empty or atm_put_row.empty:
            return None

        atm_call = atm_call_row.iloc[0]
        atm_put  = atm_put_row.iloc[0]

        # Use mid price
        call_bid = float(atm_call.get("bid", 0) or 0)
        call_ask = float(atm_call.get("ask", 0) or 0)
        put_bid  = float(atm_put.get("bid",  0) or 0)
        put_ask  = float(atm_put.get("ask",  0) or 0)

        # Fall back to lastPrice if bid/ask unavailable (after hours)
        call_mid = (call_bid + call_ask) / 2 if call_ask > 0 else float(atm_call.get("lastPrice", 0) or 0)
        put_mid  = (put_bid  + put_ask)  / 2 if put_ask  > 0 else float(atm_put.get("lastPrice",  0) or 0)

        if call_mid <= 0 or put_mid <= 0:
            return None

        straddle = call_mid + put_mid
        em       = round(straddle * 0.68, 2)
        em_pct   = round(em / price * 100, 2) if price > 0 else 0
        atm_iv   = round(float(atm_call.get("impliedVolatility", 0) or 0), 4)

        return {
            "expiry":   expiry,
            "strike":   atm_strike,
            "straddle": round(straddle, 2),
            "em":       em,
            "em_pct":   em_pct,
            "upper":    round(price + em, 2),
            "lower":    round(price - em, 2),
            "atm_iv":   atm_iv,
        }
    except Exception as e:
        logger.debug(f"Straddle calc failed for {expiry}: {e}")
        return None


def get_expected_move(signal_symbol: str = "QQQ", force: bool = False) -> dict | None:
    """
    Fetch options-implied expected move for a signal symbol (QQQ or SMH).
    Cached per symbol per day.

    Returns dict with daily_em, weekly_em, exec_ticker EM values, etc.
    exec_ticker keys use the bull ticker name (tqqq_* for QQQ, soxl_* for SMH).
    """
    global _cache, _cache_date

    sym   = signal_symbol.upper()
    today = datetime.now().strftime("%Y-%m-%d")

    # Reset cache on new day
    if _cache_date != today:
        _cache      = {}
        _cache_date = today

    # Return in-memory cache
    if not force and sym in _cache:
        return _cache[sym]

    # Try disk cache
    if not force:
        disk = _load_disk_cache()
        if disk.get("date") == today and sym in disk.get("symbols", {}):
            result = disk["symbols"][sym]
            _cache[sym] = result
            return result

    pair = SIGNAL_TO_EXEC.get(sym)
    if not pair:
        logger.warning(f"No exec pair configured for {sym}")
        return None

    bull_ticker = pair["bull"]
    lev_mult    = pair["leverage"]
    exec_key    = bull_ticker.lower()   # e.g. "tqqq" or "soxl"

    try:
        import yfinance as yf

        ticker = yf.Ticker(sym)
        hist   = ticker.history(period="2d", interval="1d")
        if hist is None or hist.empty:
            logger.warning(f"{sym} history unavailable")
            return None
        price = float(hist["Close"].iloc[-1])

        expirations = ticker.options
        if not expirations:
            logger.warning(f"{sym} options unavailable")
            return None

        nearest  = expirations[0]
        next_fri = _next_friday()
        weekly_exp = next((e for e in expirations if e >= next_fri), nearest)

        daily  = _calc_straddle_em(ticker, nearest,    price)
        weekly = _calc_straddle_em(ticker, weekly_exp, price) if weekly_exp != nearest else daily

        if not daily:
            logger.warning(f"{sym} ATM straddle calculation failed")
            return None

        # Fetch exec ticker price for real dollar amounts
        exec_hist  = yf.Ticker(bull_ticker).history(period="2d", interval="1d")
        exec_price = float(exec_hist["Close"].iloc[-1]) if exec_hist is not None and not exec_hist.empty else price / 5

        exec_daily_em    = round(daily["em"] * lev_mult, 2)
        exec_daily_pct   = round(exec_daily_em / exec_price * 100, 2) if exec_price > 0 else daily["em_pct"] * lev_mult
        exec_daily_upper = round(exec_price + exec_daily_em, 2)
        exec_daily_lower = round(exec_price - exec_daily_em, 2)

        result = {
            "symbol":           sym,
            "price":            round(price, 2),
            "date":             today,

            "daily_expiry":     daily["expiry"],
            "daily_em":         daily["em"],
            "daily_em_pct":     daily["em_pct"],
            "daily_upper":      daily["upper"],
            "daily_lower":      daily["lower"],
            "daily_straddle":   daily["straddle"],
            "daily_atm_iv":     daily["atm_iv"],

            "weekly_expiry":    weekly["expiry"]  if weekly else daily["expiry"],
            "weekly_em":        weekly["em"]      if weekly else daily["em"],
            "weekly_em_pct":    weekly["em_pct"]  if weekly else daily["em_pct"],
            "weekly_upper":     weekly["upper"]   if weekly else daily["upper"],
            "weekly_lower":     weekly["lower"]   if weekly else daily["lower"],

            # Execution ticker fields (generic keys + symbol-specific)
            "exec_ticker":       bull_ticker,
            "exec_price":        round(exec_price, 2),
            "exec_daily_em":     exec_daily_em,
            "exec_daily_pct":    exec_daily_pct,
            "exec_daily_upper":  exec_daily_upper,
            "exec_daily_lower":  exec_daily_lower,

            # Legacy QQQ-compatible keys (for backward compat)
            f"{exec_key}_price":       round(exec_price, 2),
            f"{exec_key}_daily_em":    exec_daily_em,
            f"{exec_key}_daily_pct":   exec_daily_pct,
            f"{exec_key}_daily_upper": exec_daily_upper,
            f"{exec_key}_daily_lower": exec_daily_lower,
            # Also keep tqqq_ keys for QQQ backward compat
            "tqqq_price":        round(exec_price, 2)        if sym == "QQQ" else None,
            "tqqq_daily_em":     exec_daily_em               if sym == "QQQ" else None,
            "tqqq_daily_upper":  exec_daily_upper             if sym == "QQQ" else None,
            "tqqq_daily_lower":  exec_daily_lower             if sym == "QQQ" else None,
        }

        logger.info(
            f"{sym} EM: daily ±${daily['em']:.2f} ({daily['em_pct']:.1f}%) "
            f"[${daily['lower']:.2f}–${daily['upper']:.2f}] | "
            f"{bull_ticker} ±${exec_daily_em:.2f} "
            f"[${exec_daily_lower:.2f}–${exec_daily_upper:.2f}]"
        )

        _cache[sym] = result
        # Save to disk cache (all symbols together)
        disk = _load_disk_cache() or {"date": today, "symbols": {}}
        disk["date"] = today
        disk.setdefault("symbols", {})[sym] = result
        _save_disk_cache(disk)
        return result

    except Exception as e:
        logger.warning(f"{sym} expected move failed: {e}")
        return None


def get_qqq_expected_move(force: bool = False) -> dict | None:
    """Backward-compatible alias for get_expected_move('QQQ')."""
    return get_expected_move("QQQ", force=force)


def get_all_expected_moves(force: bool = False) -> dict:
    """
    Fetch expected moves for all configured signal symbols.
    Returns {symbol: em_dict}.
    """
    results = {}
    for sym in SIGNAL_TO_EXEC:
        em = get_expected_move(sym, force=force)
        if em:
            results[sym] = em
    return results


def em_context_for_trade(
    em: dict,
    entry_price:  float,
    stop_price:   float,
    target_price: float,
    exec_ticker:  str = "TQQQ",
) -> dict:
    """
    Given a trade setup, evaluate it against the expected move.

    Returns a context dict with:
      - stop_vs_em:   how the stop distance compares to daily EM
      - target_vs_em: whether target exceeds the daily EM boundary
      - quality:      "wide" | "ok" | "tight" (stop sizing vs EM noise)
      - notes:        human-readable context string
    """
    if not em:
        return {"quality": "unknown", "notes": "EM data unavailable"}

    is_tqqq   = exec_ticker in ("TQQQ",)
    em_val    = em["tqqq_daily_em"]    if is_tqqq else em["daily_em"]
    em_upper  = em["tqqq_daily_upper"] if is_tqqq else em["daily_upper"]
    em_lower  = em["tqqq_daily_lower"] if is_tqqq else em["daily_lower"]

    stop_dist   = abs(entry_price - stop_price)
    target_dist = abs(target_price - entry_price)
    em_third    = em_val / 3  # stop should be > 1/3 of EM to avoid noise

    notes = []

    # Stop quality
    if stop_dist < em_third:
        quality = "tight"
        notes.append(f"Stop ±${stop_dist:.2f} < 1/3 of daily EM (${em_third:.2f}) — may be stopped out by noise")
    elif stop_dist < em_val * 0.6:
        quality = "ok"
        notes.append(f"Stop ±${stop_dist:.2f} within EM range — reasonable")
    else:
        quality = "wide"
        notes.append(f"Stop ±${stop_dist:.2f} > daily EM — wide stop, larger risk")

    # Target vs EM boundary
    if exec_ticker == "TQQQ":
        at_boundary = abs(target_price - em_upper) < em_val * 0.1 or \
                      abs(target_price - em_lower) < em_val * 0.1
        beyond_em   = target_price > em_upper or target_price < em_lower
    else:
        at_boundary = abs(target_price - em_upper) < em_val * 0.1
        beyond_em   = target_price > em_upper

    if beyond_em:
        notes.append(f"Target ${target_price:.2f} exceeds EM boundary (${em_upper:.2f}) — ambitious but possible on trend days")
    elif at_boundary:
        notes.append(f"Target near EM boundary (${em_upper:.2f}) — consider scaling out")
    else:
        notes.append(f"Target ${target_price:.2f} within EM range")

    return {
        "quality":       quality,
        "em_val":        em_val,
        "em_upper":      em_upper,
        "em_lower":      em_lower,
        "stop_dist":     round(stop_dist, 2),
        "target_dist":   round(target_dist, 2),
        "em_third":      round(em_third, 2),
        "at_em_boundary":at_boundary,
        "beyond_em":     beyond_em,
        "notes":         " | ".join(notes),
    }