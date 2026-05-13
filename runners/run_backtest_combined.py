"""
run_backtest_combined.py — Backtest TrendFilteredORB (full pipeline)
─────────────────────────────────────────────────────────────────────
Tests the SAME strategy that runs live:
  • Multi-symbol (symbols.txt or TICKERS override below)
  • Earnings filter
  • AI regime detection (Ollama — must be running)
  • Mean-reversion fallback
  • Leveraged ETF routing via leverage_map.py

Data source: Polygon.io 5-min bars (free tier — cached after first run)
Free tier rate limit: 5 req/min → 13s delay between pages.
First run: ~30–60 min. Subsequent runs: instant (loads from cache).

Configure the date range and symbols below.
"""

import os
import sys
import time
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()  # Must be before ALL lumibot imports

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lumibot.backtesting import PandasDataBacktesting
from lumibot.entities import Asset, Data
from strategies.trend_filtered_orb import TrendFilteredORB

# ── Configuration ──────────────────────────────────────────────────────────────
# Date range for the backtest
START = datetime(2024, 7, 1)
END   = datetime(2025, 7, 1)

# Symbols to fetch intraday data for.
# TrendFilteredORB reads signals from the underlying but may execute in the
# leveraged ETF — include any leveraged tickers you want routable.
# Tip: keep this focused; every ticker costs Polygon quota.
TICKERS = ["QQQ", "TQQQ", "SQQQ", "SPY", "SPXL", "SPXS", "SMH", "SOXL", "SOXS"]

# Strategy parameters — mirror run_live_combined.py
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

STARTING_CAPITAL = 10_000
CACHE_DIR        = "cache"

# Free tier: 5 requests/min → 13s between pages keeps us safe
RATE_LIMIT_DELAY = 13


# ── Polygon data fetcher ────────────────────────────────────────────────────────

def fetch_from_polygon(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """
    Fetch 5-min OHLCV from Polygon using raw requests + manual pagination.
    The polygon-api-client fires paginated requests too fast for the free tier.
    This version waits RATE_LIMIT_DELAY seconds between every page request.
    """
    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        raise ValueError("POLYGON_API_KEY not found in .env")

    all_bars = []
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/5/minute"
        f"/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={api_key}"
    )

    page = 1
    while url:
        print(f"  [{symbol}] Page {page} — requesting...")
        resp = requests.get(url, timeout=30)

        if resp.status_code == 429:
            wait = 60
            print(f"  429 Rate limited — waiting {wait}s...")
            time.sleep(wait)
            continue  # retry same URL

        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        all_bars.extend(results)
        print(f"  [{symbol}] Page {page}: {len(results)} bars "
              f"(running total: {len(all_bars)})")

        next_url = data.get("next_url")
        if next_url:
            url = f"{next_url}&apiKey={api_key}"
            page += 1
            print(f"  Waiting {RATE_LIMIT_DELAY}s (free tier rate limit)...")
            time.sleep(RATE_LIMIT_DELAY)
        else:
            url = None

    if not all_bars:
        raise ValueError(f"No Polygon data returned for {symbol}")

    df = pd.DataFrame(all_bars)
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.rename(columns={
        "o": "open", "h": "high",
        "l": "low",  "c": "close", "v": "volume"
    })
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    df.index = df.index.tz_convert("America/New_York")
    df = df.between_time("09:30", "16:00")  # regular hours only

    print(f"  ✅ {symbol}: {len(df)} bars ready")
    return df


def get_cached_data(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Load from CSV cache or fetch from Polygon if not cached."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{symbol}_{start.date()}_{end.date()}_5min.csv")

    if os.path.exists(path):
        print(f"📂 {symbol}: loading from cache ({path})")
        df = pd.read_csv(path, index_col=0, parse_dates=True)

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert("America/New_York")
        return df

    print(f"🌐 {symbol}: not cached, fetching from Polygon...")
    df = fetch_from_polygon(symbol, start, end)
    df.to_csv(path)
    print(f"💾 {symbol}: cached to {path}")
    print(f"⏳ Waiting 15s before next ticker...")
    time.sleep(15)
    return df


def build_pandas_data(tickers, start, end):
    """Build {Asset: Data} dict for PandasDataBacktesting."""
    pandas_data = {}
    for ticker in tickers:
        df = get_cached_data(ticker, start, end)
        asset = Asset(symbol=ticker, asset_type="stock")
        pandas_data[asset] = Data(asset, df, timestep="minute")
        print(f"✅ {ticker} loaded: {len(df)} bars "
              f"({df.index[0].date()} → {df.index[-1].date()})")
    return pandas_data


# ── Main ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("  TREND-FILTERED ORB — FULL PIPELINE BACKTEST")
    print("=" * 70)
    print(f"  Period         : {START.date()} → {END.date()}")
    print(f"  Tickers        : {TICKERS}")
    print(f"  Starting Cap   : ${STARTING_CAPITAL:,}")
    print(f"  AI Confidence  : {PARAMS['ai_min_confidence']} (requires Ollama running)")
    print()
    print("  NOTE: This backtests TrendFilteredORB — the same strategy as live.")
    print("        Ollama must be running (ollama serve) for the AI grader to work.")
    print("        If Ollama is down, ai_engine falls back gracefully to confidence=0.6.")
    print()
    print("  First run fetches 5-min Polygon data (free tier ~13s/page). Grab a coffee.")
    print("  Subsequent runs load from cache instantly.")
    print("=" * 70 + "\n")

    pandas_data = build_pandas_data(TICKERS, START, END)

    pd.options.mode.chained_assignment = None
    print("\n🚀 Starting Backtest...")

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

    print("\n" + "=" * 70)
    print("BACKTEST COMPLETE — check logs/ for tearsheet and trade CSV")
    print("=" * 70)