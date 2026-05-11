"""
Run the EMA Crossover strategy LIVE against Alpaca (paper or real).
Switch ALPACA_IS_PAPER=false in .env to go live.
"""

import os
from dotenv import load_dotenv
from lumibot.brokers import Alpaca
from lumibot.traders import Trader
from strategies.ema_crossover_strategy import EMACrossoverStrategy

load_dotenv()

BROKER_CONFIG = {
    "API_KEY":    os.getenv("ALPACA_API_KEY"),
    "API_SECRET": os.getenv("ALPACA_API_SECRET"),
    "PAPER":      os.getenv("ALPACA_IS_PAPER", "true").lower() == "true",
}

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
    broker   = Alpaca(BROKER_CONFIG)
    strategy = EMACrossoverStrategy(
        broker=broker,
        parameters=PARAMS,
    )
    trader = Trader()
    trader.add_strategy(strategy)
    trader.run_all()