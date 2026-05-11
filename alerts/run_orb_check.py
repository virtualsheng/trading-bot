"""
Morning ORB (Opening Range Breakout) check.
Run via cron at 9:45am EST on weekdays:
  45 9 * * 1-5 python alerts/run_orb_check.py

Checks if QQQ has broken above or below its first 15-min range.
This is the morning version of the 9:15-9:45am alerts.
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from datetime import datetime
from strategies.signal_engine import get_orb_signal

load_dotenv()

API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

SYMBOLS = ["QQQ", "SPY", "SMH"]

def main():
    print(f"\n{'='*60}")
    print(f"ORB SIGNALS (9:45am) — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    for symbol in SYMBOLS:
        result = get_orb_signal(symbol, API_KEY, SECRET_KEY)
        signal = result.get("signal", "ERROR")
        
        if signal == "BUY":
            print(
                f"\033[92m{symbol}: BREAKOUT ↑\033[0m | "
                f"OR High: {result['or_high']:.2f} | "
                f"Current: {result['current']:.2f} | "
                f"Stop: {result['stop_loss']:.2f}"
            )
        elif signal == "SELL":
            print(
                f"\033[91m{symbol}: BREAKDOWN ↓\033[0m | "
                f"OR Low: {result['or_low']:.2f} | "
                f"Current: {result['current']:.2f} | "
                f"Stop: {result['stop_loss']:.2f}"
            )
        else:
            print(f"{symbol}: WAIT — {result.get('reason', 'inside range')}")
    
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()