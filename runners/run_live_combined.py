"""
Run the Combined Trend-Filtered ORB strategy LIVE against Alpaca.
This script replaces both run_live_orb.py and run_live_ema.py by
using the afternoon technical signals to filter morning ORB trades.
Uses Previous Day Technical Signal as Primary Bias
"""

import os
from dotenv import load_dotenv
from lumibot.brokers import Alpaca
from lumibot.traders import Trader
from strategies.trend_filtered_orb import TrendFilteredORB

load_dotenv()

BROKER_CONFIG = {
    "API_KEY": os.getenv("ALPACA_API_KEY"),
    "API_SECRET": os.getenv("ALPACA_API_SECRET"),
    "PAPER": os.getenv("ALPACA_IS_PAPER", "true").lower() == "true",
}

PARAMS = {
    "underlying": "QQQ",
    "bull_ticker": "TQQQ",
    "bear_ticker": "SQQQ",
    "orb_minutes": 15,
    "bar_minutes": 5,
    "risk_pct": 0.01,
    "reward_ratio": 2.0,
    "eod_exit_time": "15:45",
}

def main():
    if not BROKER_CONFIG["API_KEY"] or not BROKER_CONFIG["API_SECRET"]:
        print("❌ Missing Alpaca credentials in .env")
        return

    broker = Alpaca(BROKER_CONFIG)

    strategy = TrendFilteredORB(
        broker=broker,
        parameters=PARAMS,
        name="TrendFilteredORB_Live",
    )

    trader = Trader()
    trader.add_strategy(strategy)

    print("=" * 90)
    print("🚀 LIVE COMBINED STRATEGY STARTED")
    print("=" * 90)
    print(f"Mode          : {'PAPER TRADING' if BROKER_CONFIG['PAPER'] else 'LIVE TRADING ⚠️'}")
    print(f"Primary Bias  : Previous Day Technical Signal")
    print(f"Execution     : Morning ORB")
    print(f"Risk per Trade: {PARAMS['risk_pct']*100}%")
    print("=" * 90)

    trader.run_all()


if __name__ == "__main__":
    main()