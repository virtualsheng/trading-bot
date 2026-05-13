"""
run_backtest_combined.py — Backtest TrendFilteredORB (full pipeline)
─────────────────────────────────────────────────────────────────────
Tests the SAME strategy that runs live:
  • Multi-symbol (all symbols from symbols.txt + their leveraged pairs)
  • Earnings filter
  • AI regime detection (Ollama — must be running)
  • Mean-reversion fallback
  • Leveraged ETF routing via leverage_map.py

Data source: Polygon.io 5-min bars (free tier — cached after first run)
Free tier rate limit: 5 req/min → 13s delay between pages.
First run: ~30–90 min depending on symbol count. Subsequent runs: instant.

BACKTEST-MODE BIAS:
  TrendFilteredORB._run_eod_signals() calls the live Alpaca API, which
  returns real-world signals for today — not the simulated backtest date.
  To work around this, we pre-generate a neutral HOLD bias for all symbols
  and write it to cache/daily_bias.json before the backtest starts.
  We also set LUMIBOT_BACKTEST_MODE=true so the strategy skips live API
  calls for bias generation and instead derives signals from simulated bars.

Configure START/END and STARTING_CAPITAL below.
"""

import os
import sys
import time
import requests
import pandas as pd
import json
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()  # Must be before ALL lumibot imports

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Signal that we're in backtest mode — suppresses live Alpaca API calls
# inside TrendFilteredORB._run_eod_signals()
os.environ["LUMIBOT_BACKTEST_MODE"] = "true"

from lumibot.backtesting import PandasDataBacktesting
from lumibot.entities import Asset, Data
from strategies.trend_filtered_orb import TrendFilteredORB

# ── Configuration ──────────────────────────────────────────────────────────────
START            = datetime(2024, 7, 1)
END              = datetime(2025, 7, 1)
STARTING_CAPITAL = 10_000
CACHE_DIR        = "cache"

# All signal symbols from symbols.txt + all their leveraged execution tickers
# from leverage_map.py. The strategy needs data for every ticker it might trade.
SIGNAL_TICKERS = [
    "SPY", "QQQ", "SPMO", "QQQM",          # Broad market
    "SMH", "NVDA", "MU", "AMAT", "LRCX",   # Semiconductors
    "TSM", "SNDK", "DRAM",
    "GLDM", "PSLV", "GDXJ", "GDMN",        # Precious metals
    "GDE", "ARIS", "AG", "PAAS", "SLVP",
    "IBIT",                                  # Crypto
    "JPM",                                   # Financials
    "PLTR", "ROBO",                          # Tech/AI
    "NANR", "DBC", "REMX",                  # Commodities
    "UFO", "RKLB",                           # Space/Defense
    "URA", "URNM",                           # Uranium
    "EWT", "EWJV",                           # International
    "DBMF", "GRID", "CEG",                  # Alternatives
]

LEVERAGED_TICKERS = [
    "TQQQ", "SQQQ",         # Nasdaq 3x
    "SPXL", "SPXS",         # S&P 500 3x
    "SOXL", "SOXS",         # Semiconductor 3x
    "NVDL", "NVDD",         # NVDA 2x
    "TSMU",                  # TSM 2x
    "UGL",  "GLL",          # Gold 2x
    "AGQ",  "ZSL",          # Silver 2x
    "JNUG", "JDST",         # Junior gold miners 2x
    "BITX",                  # Bitcoin 2x
    "FAS",  "FAZ",          # Financials 3x
    "ERX",  "ERY",          # Energy 2x
    "PTIR",                  # PLTR 2x
]

# Combine — deduplicated, preserving order
ALL_TICKERS = list(dict.fromkeys(SIGNAL_TICKERS + LEVERAGED_TICKERS))

# Free tier: 5 requests/min → 13s between pages keeps us safe
RATE_LIMIT_DELAY = 13


# ── Backtest-mode bias ──────────────────────────────────────────────────────────

def write_neutral_bias(symbols: list, start: datetime):
    """
    Write a neutral HOLD bias for all symbols to the cache before the backtest.

    Why: TrendFilteredORB._run_eod_signals() normally hits the live Alpaca API
    to generate signals. During backtesting that returns today's real prices,
    not simulated prices from the backtest period. We pre-seed the cache with
    HOLD so no stale live-data signals leak into the simulation. The strategy
    will update the bias naturally as it processes each simulated day.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    bias = {}
    for symbol in symbols:
        bias[symbol] = {
            "action":     "HOLD",
            "bull_score": 0,
            "bear_score": 0,
            "rsi":        50.0,
            "vol_ratio":  1.0,
            "date":       start.strftime("%Y-%m-%d"),
            "source":     "BACKTEST_INIT",
        }
    path = os.path.join(CACHE_DIR, "daily_bias.json")
    with open(path, "w") as f:
        json.dump(bias, f, indent=2)
    print(f"✅ Wrote neutral bias for {len(bias)} symbols → {path}")


# ── Polygon data fetcher ────────────────────────────────────────────────────────

def fetch_from_polygon(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
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
            continue

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
    df = df.between_time("09:30", "16:00")
    print(f"  ✅ {symbol}: {len(df)} bars ready")
    return df


def get_cached_data(symbol: str, start: datetime, end: datetime):
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
    try:
        df = fetch_from_polygon(symbol, start, end)
        df.to_csv(path)
        print(f"💾 {symbol}: cached to {path}")
        print(f"⏳ Waiting 15s before next ticker...")
        time.sleep(15)
        return df
    except ValueError as e:
        print(f"  ⚠️  {symbol}: skipping — {e}")
        return None


def build_pandas_data(tickers, start, end):
    """Build {Asset: Data} dict for PandasDataBacktesting. Skips missing tickers."""
    pandas_data = {}
    skipped = []
    for ticker in tickers:
        df = get_cached_data(ticker, start, end)
        if df is None or df.empty:
            skipped.append(ticker)
            continue
        asset = Asset(symbol=ticker, asset_type="stock")
        pandas_data[asset] = Data(asset, df, timestep="minute")
        print(f"✅ {ticker} loaded: {len(df)} bars "
              f"({df.index[0].date()} → {df.index[-1].date()})")

    if skipped:
        print(f"\n⚠️  Skipped {len(skipped)} tickers with no Polygon data: {skipped}")
        print("   Strategy will gracefully skip these symbols during simulation.\n")

    return pandas_data


# ── Main ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("  TREND-FILTERED ORB — FULL PIPELINE BACKTEST")
    print("=" * 70)
    print(f"  Period         : {START.date()} → {END.date()}")
    print(f"  Signal tickers : {len(SIGNAL_TICKERS)}")
    print(f"  Leveraged ETFs : {len(LEVERAGED_TICKERS)}")
    print(f"  Total tickers  : {len(ALL_TICKERS)}")
    print(f"  Starting Cap   : ${STARTING_CAPITAL:,}")
    print(f"  Backtest mode  : live Alpaca API calls suppressed")
    print()
    print("  Ollama must be running (ollama serve) for AI grading.")
    print("  If Ollama is down, ai_engine falls back gracefully to confidence=0.6.")
    print()
    print("  First run fetches 5-min Polygon data for all tickers.")
    print("  Free tier (~13s/page) — expect 60–90 min for a full fetch.")
    print("  Subsequent runs load from cache instantly.")
    print("=" * 70 + "\n")

    # Pre-seed neutral bias so no stale live-API data leaks into the simulation
    write_neutral_bias(SIGNAL_TICKERS, START)

    pandas_data = build_pandas_data(ALL_TICKERS, START, END)

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