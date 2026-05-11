"""
Run the Combined Trend-Filtered ORB strategy LIVE against Alpaca.
This script replaces both run_live_orb.py and run_live_ema.py by
using the afternoon technical signals to filter morning ORB trades.
"""

import os
from dotenv import load_dotenv
from lumibot.brokers import Alpaca
from lumibot.traders import Trader
from strategies.trend_filtered_orb import TrendFilteredORB

# Load credentials from .env
load_dotenv()

BROKER_CONFIG = {
    "API_KEY":    os.getenv("ALPACA_API_KEY"),
    "API_SECRET": os.getenv("ALPACA_API_SECRET"),
    "PAPER":      os.getenv("ALPACA_IS_PAPER", "true").lower() == "true",
}

# Combined Parameters
# These merge the settings from both your previous scripts
PARAMS = {
    "underlying":    "QQQ",      # The asset to analyze for signals
    "bull_ticker":   "TQQQ",     # Long vehicle
    "bear_ticker":   "SQQQ",     # Short vehicle
    "orb_minutes":   15,         # Duration of the morning range
    "bar_minutes":   5,          # Candle size for entries
    "risk_pct":      0.01,       # Risk 1% of equity per trade
    "reward_ratio":  2.0,        # 2:1 Take Profit ratio
    "eod_exit_time": "15:45",    # Hard exit to avoid overnight risk
}

def main():
    # 1. Initialize the Broker
    broker = Alpaca(BROKER_CONFIG)

    # 2. Initialize the Strategy
    # This uses TrendFilteredORB which contains the "Priority Rule" logic
    strategy = TrendFilteredORB(
        broker=broker,
        parameters=PARAMS,
    )

    # 3. Start the Trader
    trader = Trader()
    trader.add_strategy(strategy)
    
    print(f"--- Starting Combined Strategy ---")
    print(f"Mode: {'PAPER' if BROKER_CONFIG['PAPER'] else 'LIVE'}")
    print(f"Symbol: {PARAMS['underlying']} (Trading {PARAMS['bull_ticker']}/{PARAMS['bear_ticker']})")
    
    trader.run_all()

if __name__ == "__main__":
    main()