"""
run_backtest_ema.py — EMA Crossover Strategy backtest
───────────────────────────────────────────────────────
Uses Yahoo Finance daily data (free, no API key needed).
3-year window includes the 2022 bear market, 2023 recovery, 2024–2025 bull run.

Change BACKTESTING_START/END to test different periods.
"""
import os
from dotenv import load_dotenv
load_dotenv()  # Must be before ALL lumibot imports

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from lumibot.backtesting import YahooDataBacktesting
from strategies.ema_crossover_strategy import EMACrossoverStrategy

# ── Backtest configuration ─────────────────────────────────────────────────────
BACKTESTING_START = datetime(2022, 1, 1)
BACKTESTING_END   = datetime(2025, 1, 1)
STARTING_CAPITAL  = 10_000   # Match your actual account size for realistic results

PARAMS = {
    "underlying":     "QQQ",
    "bull_ticker":    "TQQQ",
    "bear_ticker":    "SQQQ",
    "ema_fast":       2,
    "ema_mid":        3,
    "ema_slow":       5,
    "rsi_period":     14,
    "rsi_oversold":   40,
    "rsi_overbought": 70,
    "position_pct":   0.95,
    "lookback_days":  60,
    "min_bull_score": 4,
    "min_bear_score": 4,
}

if __name__ == "__main__":
    print("=" * 60)
    print("EMA CROSSOVER BACKTEST")
    print(f"Period         : {BACKTESTING_START.date()} → {BACKTESTING_END.date()}")
    print(f"Starting Capital: ${STARTING_CAPITAL:,}")
    print("=" * 60 + "\n")

    EMACrossoverStrategy.run_backtest(
        datasource_class=YahooDataBacktesting,
        backtesting_start=BACKTESTING_START,
        backtesting_end=BACKTESTING_END,
        parameters=PARAMS,
        initial_portfolio_value=STARTING_CAPITAL,
        benchmark_asset="QQQ",
        show_plot=True,
        show_tearsheet=True,
        save_tearsheet=True,
    )

    print("\n" + "=" * 60)
    print("EMA CROSSOVER BACKTEST COMPLETE")
    print("Check logs/ for tearsheet, equity curve, and trade CSV")
    print("=" * 60)