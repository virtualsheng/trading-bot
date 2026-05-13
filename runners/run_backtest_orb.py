"""
run_backtest_orb.py — Base ORB Strategy backtest (Polygon.io intraday)
────────────────────────────────────────────────────────────────────────
Backtests the standalone ORBStrategy (no AI, no regime filter, no earnings).
Useful as a baseline to compare against TrendFilteredORB (run_backtest_combined.py).

Requires POLYGON_API_KEY in .env.
LumiBot's PolygonDataBacktesting handles pagination and caching internally.
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()  # Must be before ALL lumibot imports

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from lumibot.backtesting import PolygonDataBacktesting
from strategies.orb_strategy import ORBStrategy

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
if not POLYGON_API_KEY:
    raise ValueError("POLYGON_API_KEY not set in .env")

# ── Date range ─────────────────────────────────────────────────────────────────
END   = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
START = END - timedelta(days=365)

STARTING_CAPITAL = 10_000

PARAMS = {
    "underlying":     "QQQ",
    "bull_ticker":    "TQQQ",
    "bear_ticker":    "SQQQ",
    "orb_minutes":    15,
    "bar_minutes":    5,
    "risk_pct":       0.01,
    "reward_ratio":   2.0,
    "eod_exit_time":  "15:45",
}

if __name__ == "__main__":
    print("=" * 60)
    print("BASE ORB STRATEGY BACKTEST (no AI / no regime filter)")
    print(f"Period          : {START.date()} → {END.date()}")
    print(f"Starting Capital: ${STARTING_CAPITAL:,}")
    print("Note: compare this to run_backtest_combined.py to measure")
    print("      the value added by the AI + regime filter layer.")
    print("=" * 60 + "\n")

    # LumiBot's PolygonDataBacktesting reads POLYGON_API_KEY from env automatically.
    # run_backtest() returns the Strategy instance (not a dict) — stats are
    # printed by LumiBot to the tearsheet and logs.
    ORBStrategy.run_backtest(
        datasource_class=PolygonDataBacktesting,
        backtesting_start=START,
        backtesting_end=END,
        parameters=PARAMS,
        initial_portfolio_value=STARTING_CAPITAL,
        benchmark_asset="QQQ",
        show_plot=True,
        show_tearsheet=True,
        save_tearsheet=True,
        polygon_api_key=POLYGON_API_KEY,
    )

    print("\n" + "=" * 60)
    print("BASE ORB BACKTEST COMPLETE")
    print("Check logs/ for tearsheet and trade CSV")
    print("=" * 60)