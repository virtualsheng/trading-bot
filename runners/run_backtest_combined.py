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
START            = datetime(2024, 5, 15)
END              = datetime(2026, 5, 15)

# Match your actual live account size for realistic position sizing validation.
# risk_pct=0.10, max_position_pct=1.0 → fills account up to value cap.
STARTING_CAPITAL = 2_000
CACHE_DIR        = "cache"

# ── Signal symbols — mirrors live account ────────────────────────────────────
# QQQ → TQQQ (bull) / SQQQ (bear)
# SMH → SOXL (bull) / SOXS (bear)
TICKERS = ["QQQ", "TQQQ", "SQQQ", "SMH", "SOXL", "SOXS"]

# ── Backtest-specific parameters ────────────────────────────────────────────
# These OVERRIDE the strategy's live defaults for the backtest run only.
# Must mirror run_live_combined.py exactly so backtest results are comparable.
PARAMS = {
    # ── Core ORB ────────────────────────────────────────────────────────────
    "orb_minutes":        15,
    "bar_minutes":        5,
    "risk_pct":           0.10,    # 10% max loss per trade = $200 on $2k
    "reward_ratio":       2.0,
    "eod_exit_time":      "15:50",   # 3:50 PM - close at market hours

    # ── Position limits ──────────────────────────────────────────────────────
    # 2 positions max — one per symbol (QQQ and SMH).
    # Capital split proportional to conviction score at entry time.
    # max_position_pct=1.0 → full account deployable across positions.
    "max_positions":      2,
    "max_position_pct":   1.0,

    # ── AI / signal ─────────────────────────────────────────────────────────
    "ai_min_confidence":  0.55,
    "hold_override":      False,
    "hold_override_size": 0.5,

    # ── Stop placement ───────────────────────────────────────────────────────
    "stop_mode":           "or_low",
    "stop_delay_minutes":  15,
    "min_stop_pct":        0.005,

    # ── Trail-only exit ──────────────────────────────────────────────────────
    "target_exit":        False,  # let trail + EOD handle exit
    "target_scale_out":   1.0,    # unused when target_exit=False
    "trail_stop_pct":     0.02,   # 2% trailing stop
    "em_boundary_exit":   False,  # skip in backtest - no historical options data

    # ── Breakout filter ──────────────────────────────────────────────────────
    "min_breakout_pct":   0.001,  # price must clear OR high by at least 0.1%

    # ── VIX filter (#4) — DISABLED in backtest ───────────────────────────────
    # Historical ^VIX is available via yfinance but adds API complexity and
    # slows the backtest. Set vix_skip_above=999 to disable so backtest results
    # are comparable to pre-filter baseline. Enable for VIX-filtered backtest.
    "vix_skip_above":      999,   # effectively disabled in backtest
    "vix_half_size_above": 999,   # effectively disabled in backtest

    # ── HOLD-bias volume gate (#6) ────────────────────────────────────────────
    "hold_min_vol_ratio":  1.2,   # same as live — test its effect in backtest
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
        budget=STARTING_CAPITAL,
        show_plot=True,
        show_tearsheet=True,
        save_tearsheet=True,
    )

    print("\n" + "=" * 60)
    print("BACKTEST COMPLETE — check logs/ for tearsheet and trades CSV")
    print("=" * 60)