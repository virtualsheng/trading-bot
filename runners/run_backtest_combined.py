"""
run_backtest_combined.py — Focused pipeline validation backtest
────────────────────────────────────────────────────────────────
Tests QQQ → TQQQ / SQQQ only.
Purpose: validate that TrendFilteredORB's signal flow, AI grading,
regime detection, and ORB execution logic all work correctly —
without spending 90 minutes fetching data for 50+ tickers.

Data source: Polygon.io 5-min bars (3 tickers, cached after first run).
First run: ~5–10 min. Subsequent runs: instant from cache.

BACKTEST-MODE BIAS:
  _run_eod_signals() normally calls the live Alpaca API, which returns
  today's real prices — not simulated backtest prices. We suppress that
  by setting LUMIBOT_BACKTEST_MODE=true and pre-seeding the bias cache
  with a neutral HOLD. The strategy will update bias each simulated EOD
  using the backtest engine's bar data via _run_eod_signals_backtest().

PARAMETER PHILOSOPHY:
  The PARAMS dict here mirrors the $2,000 live ORB account (run_live_combined.py).
  Single symbol (QQQ→TQQQ/SQQQ), $2k starting capital, 1 max position, 40% cap.
  PDT note: use a cash account on Alpaca — no day-trading restrictions apply
  and T+1 settlement is fine for 1 trade per day.
  Do NOT copy these params into run_live_combined.py (full multi-symbol account).
"""

import os
import sys
import time
import json
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["LUMIBOT_BACKTEST_MODE"] = "true"

from lumibot.backtesting import PandasDataBacktesting
from lumibot.entities import Asset, Data
from strategies.trend_filtered_orb import TrendFilteredORB

# ── Date range ──────────────────────────────────────────────────────────────
# Free Polygon tier: data available from roughly 2 years ago to present.
# Best down-market period available: Apr 2025 tariff crash + Iran War volatility.
# Adjust START/END to the period you want to test.
START            = datetime(2025, 3, 1)
END              = datetime(2025, 5, 15)

# Match your actual live account size for realistic position sizing validation.
# With $2,000 and max_position_pct=0.40, each position is capped at $800.
# TQQQ at ~$33 = ~24 shares per trade — realistic for a $2k cash account.
STARTING_CAPITAL = 2_000
CACHE_DIR        = "cache"

# ── Single symbol — mirrors the $2k live ORB account ────────────────────────
# QQQ is the signal symbol; the strategy maps it to TQQQ (bull) / SQQQ (bear).
# Only these 3 tickers need to be fetched and loaded.
TICKERS = ["QQQ", "TQQQ", "SQQQ"]

# ── Backtest-specific parameters ────────────────────────────────────────────
# These OVERRIDE the strategy's live defaults for the backtest run only.
# Mirrors the $2k live ORB account configuration in run_live_combined.py.
# Do NOT copy these into run_live_combined.py (full multi-symbol live account).
PARAMS = {
    # ── Core ORB ────────────────────────────────────────────────────────────
    "orb_minutes":        15,
    "bar_minutes":        5,
    "risk_pct":           0.02,    # 2% risk per trade = $40 on a $2k account
    "reward_ratio":       2.0,     # 2:1 reward:risk → $80 target per trade
    "eod_exit_time":      "15:45",

    # ── Position limits ($2k single-symbol account) ──────────────────────────
    # 1 position max — only trading QQQ→TQQQ/SQQQ, no need for more slots.
    # PDT note: Alpaca cash account settles T+1 — safe for 1 trade/day with no
    # day-trading restrictions (PDT rule applies to margin accounts only).
    "max_positions":      1,
    "max_position_pct":   0.40,    # 40% cap = $800 max position on $2k account

    # ── AI / signal ─────────────────────────────────────────────────────────
    "ai_min_confidence":  0.55,
    "hold_override":      False,
    "hold_override_size": 0.5,

    # ── Stop placement (v15) ─────────────────────────────────────────────────
    "stop_mode":           "or_low", # stop at OR low — textbook ORB placement
    "stop_delay_minutes":  15,       # ignore stop for first 15 min after entry
    "min_stop_pct":        0.005,    # floor; scaled ×3 for TQQQ/SQQQ = 1.5%

    # ── Trail-only exit (v17) ────────────────────────────────────────────
    # target_exit=False: no hard target close. Trail + EOD handles all exits.
    # 2% trail on TQQQ/SQQQ sits above normal intrabar noise (~0.9-1.5%)
    # while catching genuine reversals. Mirrors run_live_combined.py ORB mode.
    "target_exit":        False,  # let trail + EOD handle exit
    "target_scale_out":   1.0,    # unused when target_exit=False
    "trail_stop_pct":     0.02,   # 2% trailing stop

    # ── Breakout filter ──────────────────────────────────────────────────────
    "min_breakout_pct":   0.001,  # price must clear OR high by at least 0.1%
}

RATE_LIMIT_DELAY = 13  # seconds between Polygon API pages (free tier: 5 req/min)


# ── Bias seed ────────────────────────────────────────────────────────────────

def write_neutral_bias(symbols: list, start: datetime):
    """
    Pre-seed the bias cache with HOLD for all symbols before the backtest.
    Prevents stale live-API signals from leaking into the simulation.
    The strategy updates this each simulated EOD via _run_eod_signals_backtest().
    """
    os.makedirs("cache", exist_ok=True)
    bias = {
        s: {
            "action":     "HOLD",
            "bull_score": 0,
            "bear_score": 0,
            "rsi":        50,
            "vol_ratio":  1.0,
            "date":       str(start.date()),
            "source":     "backtest_seed",
        }
        for s in symbols
    }
    path = "cache/daily_bias_backtest.json"
    with open(path, "w") as f:
        json.dump(bias, f, indent=2)
    print(f"✅ Neutral bias written for {len(symbols)} symbols → {path}")


# ── Polygon data fetcher ─────────────────────────────────────────────────────

def fetch_from_polygon(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        raise ValueError("POLYGON_API_KEY not set in .env")

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/5/minute/"
        f"{start.date()}/{end.date()}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={api_key}"
    )

    all_bars = []
    page = 1
    while url:
        print(f"  [{symbol}] Page {page}...")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        bars = data.get("results", [])
        all_bars.extend(bars)
        next_url = data.get("next_url")
        if next_url:
            url = f"{next_url}&apiKey={api_key}"
            page += 1
            time.sleep(RATE_LIMIT_DELAY)
        else:
            url = None

    if not all_bars:
        raise ValueError(f"No data returned for {symbol}")

    df = pd.DataFrame(all_bars)
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    df.index = df.index.tz_convert("America/New_York")
    df = df.between_time("09:30", "16:00")
    print(f"  ✅ {symbol}: {len(df)} bars")
    return df


def get_cached_data(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{symbol}_{start.date()}_{end.date()}_5min.csv")

    if os.path.exists(path):
        print(f"📂 {symbol}: from cache")
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("America/New_York")
        return df

    print(f"🌐 {symbol}: fetching from Polygon...")
    df = fetch_from_polygon(symbol, start, end)
    df.to_csv(path)
    print(f"💾 {symbol}: cached → {path}")
    time.sleep(15)
    return df


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TREND-FILTERED ORB — PIPELINE VALIDATION BACKTEST")
    print("=" * 60)
    print(f"  Symbols        : {TICKERS}")
    print(f"  Period         : {START.date()} → {END.date()}")
    print(f"  Starting Cap   : ${STARTING_CAPITAL:,}")
    print(f"  Backtest mode  : live Alpaca API suppressed")
    print(f"  Ollama         : AI grading skipped (BACKTEST_MODE=true)")
    print(f"  PDT note       : use Alpaca cash account in live (T+1 settlement)")
    print(f"  Stop mode      : {PARAMS['stop_mode']} (delay {PARAMS['stop_delay_minutes']} min)")
    print(f"  Trail stop     : {int(PARAMS['trail_stop_pct']*100)}% trailing stop (no hard target exit)")
    print(f"  Max positions  : {PARAMS['max_positions']} | "
          f"Max pos size: {int(PARAMS['max_position_pct']*100)}%")
    print("=" * 60 + "\n")

    write_neutral_bias(["QQQ"], START)

    pandas_data = {}
    for ticker in TICKERS:
        df    = get_cached_data(ticker, START, END)
        asset = Asset(symbol=ticker, asset_type="stock")
        pandas_data[asset] = Data(asset, df, timestep="minute")
        print(f"✅ {ticker}: {len(df)} bars ({df.index[0].date()} → {df.index[-1].date()})")

    pd.options.mode.chained_assignment = None
    print("\n🚀 Starting backtest...")

    TrendFilteredORB.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=START,
        backtesting_end=END,
        pandas_data=pandas_data,
        parameters=PARAMS,
        initial_portfolio_value=STARTING_CAPITAL,
        show_plot=True,
        show_tearsheet=True,
        save_tearsheet=True,
    )

    print("\n" + "=" * 60)
    print("BACKTEST COMPLETE — check logs/ for tearsheet and trades CSV")
    print("=" * 60)