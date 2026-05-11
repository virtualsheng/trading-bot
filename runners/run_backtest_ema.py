"""
Run a backtest of the EMA Crossover strategy.
Uses Yahoo Finance data (free, no API key needed).
Change the dates to test different time periods.
"""
import os
from dotenv import load_dotenv
load_dotenv()  # Must be before ALL other imports

from datetime import datetime
from lumibot.backtesting import BacktestingBroker, YahooDataBacktesting
from lumibot.traders import Trader
from strategies.ema_crossover_strategy import EMACrossoverStrategy

# ── Backtest configuration ────────────────────────────────────────────────
BACKTESTING_START = datetime(2022, 1, 1)
BACKTESTING_END   = datetime(2025, 1, 1)
STARTING_CAPITAL  = 2000

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
    # ── Set up backtesting broker using Yahoo Finance (no API keys needed) ─
    data_source = YahooDataBacktesting(
        datetime_start=BACKTESTING_START,
        datetime_end=BACKTESTING_END,
    )
    broker = BacktestingBroker(data_source)

    # ── Instantiate strategy with backtesting broker ───────────────────────
    strategy = EMACrossoverStrategy(
        broker=broker,
        parameters=PARAMS,
    )

    # ── Run backtest ───────────────────────────────────────────────────────
    trader = Trader(backtest=True)
    trader.add_strategy(strategy)
    strategy_executors = trader.run_all()

    print("\n" + "="*50)
    print("EMA CROSSOVER BACKTEST COMPLETE")
    print(f"Period: {BACKTESTING_START.date()} → {BACKTESTING_END.date()}")
    print(f"Starting Capital: ${STARTING_CAPITAL:,}")
    print("Check the logs/ folder for detailed results and charts")
    print("="*50)