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

# ── Configuration ──────────────────────────────────────────────────────────────
START            = datetime(2024, 7, 1)
END              = datetime(2025, 7, 1)
STARTING_CAPITAL = 10_000
CACHE_DIR        = "cache"

TICKERS = ["QQQ", "TQQQ", "SQQQ"]

PARAMS = {
    "orb_minutes":        15,
    "bar_minutes":        5,
    "risk_pct":           0.01,
    "reward_ratio":       2.0,
    "eod_exit_time":      "15:45",
    "max_positions":      8,
    "ai_min_confidence":  0.55,
    "hold_override":      False,
    "hold_override_size": 0.5,
}

RATE_LIMIT_DELAY = 13


# ── Bias seed ──────────────────────────────────────────────────────────────────

def write_neutral_bias(symbols: list, start: datetime):
    """
    Pre-seed the bias cache with HOLD for all symbols before the backtest.
    Prevents stale live-API signals from leaking into the simulation.
    The strategy updates this each simulated EOD via _run_eod_signals_backtest().
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    bias = {
        s: {
            "action": "HOLD", "bull_score": 0, "bear_score": 0,
            "rsi": 50.0, "vol_ratio": 1.0,
            "date": start.strftime("%Y-%m-%d"), "source": "BACKTEST_INIT",
        }
        for s in symbols
    }
    path = os.path.join(CACHE_DIR, "daily_bias.json")
    with open(path, "w") as f:
        json.dump(bias, f, indent=2)
    print(f"✅ Neutral bias written for {len(bias)} symbols → {path}")


# ── Polygon fetcher ────────────────────────────────────────────────────────────

def fetch_from_polygon(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        raise ValueError("POLYGON_API_KEY not found in .env")

    all_bars, page = [], 1
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/5/minute"
        f"/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={api_key}"
    )

    while url:
        print(f"  [{symbol}] Page {page}...")
        resp = requests.get(url, timeout=30)
        if resp.status_code == 429:
            print(f"  Rate limited — waiting 60s...")
            time.sleep(60)
            continue
        resp.raise_for_status()
        data = resp.json()
        all_bars.extend(data.get("results", []))
        print(f"  [{symbol}] {len(all_bars)} bars so far")
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


# ── Main ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TREND-FILTERED ORB — PIPELINE VALIDATION BACKTEST")
    print("=" * 60)
    print(f"  Symbols        : {TICKERS}")
    print(f"  Period         : {START.date()} → {END.date()}")
    print(f"  Starting Cap   : ${STARTING_CAPITAL:,}")
    print(f"  Backtest mode  : live Alpaca API suppressed")
    print(f"  Ollama         : must be running for AI grading")
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
    print("BACKTEST COMPLETE — check logs/ for tearsheet")
    print("=" * 60)