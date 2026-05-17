"""
expected_move.py — QQQ options-implied expected move
──────────────────────────────────────────────────────
Calculates the market's own expected daily and weekly price range
for QQQ using the ATM straddle price from the options chain.

Formula:
    Expected Move = ATM Straddle × 0.68
    ATM Straddle  = ATM call mid-price + ATM put mid-price
    × 0.68 converts 1 standard deviation to the ~68% probability range

Since the bot executes on TQQQ/SQQQ (3× leverage):
    TQQQ/SQQQ expected move ≈ QQQ expected move × 3

Used by the strategy to:
  - Validate stop distance (stop < 1/3 of daily EM = inside noise)
  - Log target vs EM boundary context
  - EOD signal report: show QQQ expected range for next session

Free — yfinance options chain, no API key required.
Cached for the session to avoid repeated fetches.

Usage:
    from strategies.expected_move import get_qqq_expected_move
    em = get_qqq_expected_move()
    if em:
        print(f"QQQ daily EM: ±${em['daily_em']:.2f}")
        print(f"QQQ range: ${em['daily_lower']:.2f} – ${em['daily_upper']:.2f}")
        print(f"TQQQ/SQQQ EM: ±${em['tqqq_daily_em']:.2f}")
"""

import json
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Simple in-memory cache — refreshed once per session
_cache: dict = {}
_cache_date: str = ""

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "cache", "expected_move_cache.json")


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


def get_qqq_expected_move(force: bool = False) -> dict | None:
    """
    Fetch QQQ options-implied expected move for today and this week.
    Cached once per day to disk — fast on repeated calls.

    Returns:
    {
        "symbol":           "QQQ",
        "price":            float,
        "date":             str,          # today YYYY-MM-DD

        # Daily (nearest expiry)
        "daily_expiry":     str,
        "daily_em":         float,        # ±$ 1-SD expected move
        "daily_em_pct":     float,        # ±%
        "daily_upper":      float,
        "daily_lower":      float,
        "daily_straddle":   float,
        "daily_atm_iv":     float,

        # Weekly (next Friday)
        "weekly_expiry":    str,
        "weekly_em":        float,
        "weekly_em_pct":    float,
        "weekly_upper":     float,
        "weekly_lower":     float,

        # TQQQ/SQQQ equivalents (QQQ × 3)
        "tqqq_daily_em":    float,
        "tqqq_daily_upper": float,
        "tqqq_daily_lower": float,
        "tqqq_price_est":   float,        # estimated TQQQ price (QQQ/5 rough estimate)
    }
    """
    global _cache, _cache_date

    today = datetime.now().strftime("%Y-%m-%d")

    # Return in-memory cache if same day
    if not force and _cache and _cache_date == today:
        return _cache

    # Try disk cache
    if not force:
        disk = _load_disk_cache()
        if disk:
            _cache      = disk
            _cache_date = today
            return disk

    try:
        import yfinance as yf

        qqq    = yf.Ticker("QQQ")
        hist   = qqq.history(period="2d", interval="1d")
        if hist is None or hist.empty:
            logger.warning("QQQ history unavailable")
            return None
        price  = float(hist["Close"].iloc[-1])

        expirations = qqq.options
        if not expirations:
            logger.warning("QQQ options unavailable")
            return None

        # Nearest expiry for daily EM
        nearest = expirations[0]

        # Next Friday for weekly EM
        next_fri   = _next_friday()
        weekly_exp = next((e for e in expirations if e >= next_fri), nearest)

        daily  = _calc_straddle_em(qqq, nearest,   price)
        weekly = _calc_straddle_em(qqq, weekly_exp, price) if weekly_exp != nearest else daily

        if not daily:
            logger.warning("QQQ ATM straddle calculation failed")
            return None

        # TQQQ/SQQQ: 3× leverage means 3× the expected move in $ terms
        # We estimate TQQQ price as roughly QQQ/5 (approximate, varies)
        tqqq_hist  = yf.Ticker("TQQQ").history(period="2d", interval="1d")
        tqqq_price = float(tqqq_hist["Close"].iloc[-1]) if tqqq_hist is not None and not tqqq_hist.empty else price / 5

        tqqq_daily_em    = round(daily["em"] * 3, 2)
        tqqq_daily_pct   = round(tqqq_daily_em / tqqq_price * 100, 2) if tqqq_price > 0 else daily["em_pct"] * 3
        tqqq_daily_upper = round(tqqq_price + tqqq_daily_em, 2)
        tqqq_daily_lower = round(tqqq_price - tqqq_daily_em, 2)

        result = {
            "symbol":           "QQQ",
            "price":            round(price, 2),
            "date":             today,

            "daily_expiry":     daily["expiry"],
            "daily_em":         daily["em"],
            "daily_em_pct":     daily["em_pct"],
            "daily_upper":      daily["upper"],
            "daily_lower":      daily["lower"],
            "daily_straddle":   daily["straddle"],
            "daily_atm_iv":     daily["atm_iv"],

            "weekly_expiry":    weekly["expiry"] if weekly else daily["expiry"],
            "weekly_em":        weekly["em"]     if weekly else daily["em"],
            "weekly_em_pct":    weekly["em_pct"] if weekly else daily["em_pct"],
            "weekly_upper":     weekly["upper"]  if weekly else daily["upper"],
            "weekly_lower":     weekly["lower"]  if weekly else daily["lower"],

            "tqqq_price":       round(tqqq_price, 2),
            "tqqq_daily_em":    tqqq_daily_em,
            "tqqq_daily_pct":   tqqq_daily_pct,
            "tqqq_daily_upper": tqqq_daily_upper,
            "tqqq_daily_lower": tqqq_daily_lower,
        }

        logger.info(
            f"QQQ expected move: "
            f"daily ±${daily['em']:.2f} ({daily['em_pct']:.1f}%) "
            f"[{daily['lower']:.2f}–{daily['upper']:.2f}] | "
            f"weekly ±${result['weekly_em']:.2f} "
            f"[{result['weekly_lower']:.2f}–{result['weekly_upper']:.2f}]"
        )
        logger.info(
            f"TQQQ implied range: "
            f"±${tqqq_daily_em:.2f} ({tqqq_daily_pct:.1f}%) "
            f"[${tqqq_daily_lower:.2f}–${tqqq_daily_upper:.2f}]"
        )

        _cache      = result
        _cache_date = today
        _save_disk_cache(result)
        return result

    except Exception as e:
        logger.warning(f"QQQ expected move failed: {e}")
        return None


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