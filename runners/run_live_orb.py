"""
Run the ORB strategy LIVE against Alpaca paper account.
"""

import os
from dotenv import load_dotenv
from lumibot.brokers import Alpaca
from lumibot.traders import Trader
from strategies.orb_strategy import ORBStrategy

load_dotenv()

BROKER_CONFIG = {
    "API_KEY":    os.getenv("ALPACA_API_KEY"),
    "API_SECRET": os.getenv("ALPACA_API_SECRET"),
    "PAPER":      os.getenv("ALPACA_IS_PAPER", "true").lower() == "true",
}

PARAMS = {
    "underlying":    "QQQ",
    "bull_ticker":   "TQQQ",
    "bear_ticker":   "SQQQ",
    "orb_minutes":   15,
    "bar_minutes":   5,
    "risk_pct":      0.01,
    "reward_ratio":  2.0,
    "eod_exit_time": "15:45",
}

if __name__ == "__main__":
    broker   = Alpaca(BROKER_CONFIG)
    strategy = ORBStrategy(
        broker=broker,
        parameters=PARAMS,
    )
    trader = Trader()
    trader.add_strategy(strategy)
    trader.run_all()