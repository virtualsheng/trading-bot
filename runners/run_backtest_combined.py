import os
import time
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()  # Must be before ALL lumibot imports

from lumibot.backtesting import PandasDataBacktesting
from lumibot.entities import Asset, Data
from strategies.orb_strategy import ORBStrategy

# ── Configuration ─────────────────────────────────────────────────────────
SYMBOL    = "QQQ"
TICKERS   = [SYMBOL, "TQQQ", "SQQQ"]
START     = datetime(2024, 7, 1)
END       = datetime(2025, 7, 1)
CACHE_DIR = "cache"

# Free tier = 5 requests/min. 13s delay between pages keeps us safe.
RATE_LIMIT_DELAY = 13


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

        # Handle rate limit explicitly
        if resp.status_code == 429:
            wait = 60
            print(f"  429 Rate limited — waiting {wait}s...")
            time.sleep(wait)
            continue  # retry same URL, don't advance page

        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        all_bars.extend(results)
        print(f"  [{symbol}] Page {page}: {len(results)} bars "
              f"(running total: {len(all_bars)})")

        # Polygon returns next_url when there are more pages
        next_url = data.get("next_url")
        if next_url:
            url = f"{next_url}&apiKey={api_key}"
            page += 1
            print(f"  Waiting {RATE_LIMIT_DELAY}s (free tier rate limit)...")
            time.sleep(RATE_LIMIT_DELAY)
        else:
            url = None  # done

    if not all_bars:
        raise ValueError(f"No Polygon data returned for {symbol}")

    # Build DataFrame
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

        # Ensure index is a proper DatetimeIndex (not plain strings)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)

        # Ensure timezone is set
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        # Normalize to Eastern time
        df.index = df.index.tz_convert("America/New_York")
        return df

    print(f"🌐 {symbol}: not cached, fetching from Polygon...")
    df = fetch_from_polygon(symbol, start, end)
    df.to_csv(path)
    print(f"💾 {symbol}: cached to {path}")

    # Wait between tickers too
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


if __name__ == "__main__":
    print("=" * 60)
    print("ORB COMBINED BACKTEST")
    print(f"Period : {START.date()} → {END.date()}")
    print(f"Symbols: {TICKERS}")
    print("First run fetches ~2 years of 5-min data from Polygon.")
    print("Free tier is slow (~13s/page). Grab a coffee.")
    print("Subsequent runs load from cache instantly.")
    print("=" * 60 + "\n")

    pandas_data = build_pandas_data(TICKERS, START, END)

    print("\n🚀 Starting Backtest...")
    ORBStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=START,
        backtesting_end=END,
        pandas_data=pandas_data,
        parameters={
            "underlying":    SYMBOL,
            "bull_ticker":   "TQQQ",
            "bear_ticker":   "SQQQ",
            "orb_minutes":   15,
            "bar_minutes":   5,
            "risk_pct":      0.01,
            "reward_ratio":  2.0,
            "eod_exit_time": "15:45",
        },
        show_plot=True,
        show_tearsheet=True,
        save_tearsheet=True,
    )

    print("\n" + "=" * 60)
    print("BACKTEST COMPLETE — check logs/ for tearsheet and trade CSV")
    print("=" * 60)