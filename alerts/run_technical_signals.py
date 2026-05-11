"""
End-of-day technical signal check.
Run via cron at 3:50pm EST on weekdays:
  50 15 * * 1-5 python alerts/run_technical_signals.py

Reads daily bars, computes EMA/RSI/MACD signals,
and prints a clear BUY/SELL/HOLD for each symbol.
This is the EOD version of the 3:50-4:15pm alerts.
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from datetime import datetime
from strategies.signal_engine import get_technical_signal

load_dotenv()

API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

# Add or remove symbols here
SYMBOLS = ["QQQ", "SPY", "TQQQ", "SQQQ", "GLDM", "SMH"]

def main():
    print(f"\n{'='*60}")
    print(f"EOD TECHNICAL SIGNALS — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    for symbol in SYMBOLS:
        result = get_technical_signal(symbol, API_KEY, SECRET_KEY)
        
        action   = result.get("action", "ERROR")
        strength = result.get("strength", "")
        bull     = result.get("bull_score", 0)
        bear     = result.get("bear_score", 0)
        rsi      = result.get("rsi", 0)
        error    = result.get("error", None)
        
        if error:
            print(f"{symbol:6s} | ERROR: {error}")
            continue
        
        # Color-code in terminal
        color = "\033[92m" if action == "BUY" else "\033[91m" if action == "SELL" else "\033[93m"
        reset = "\033[0m"
        
        print(
            f"{symbol:6s} | {color}{action:4s} {strength:8s}{reset} | "
            f"Bull:{bull}/6 Bear:{bear}/6 | RSI:{rsi:.1f} | "
            f"EMA2/3:{result.get('ema_cross_short','?'):5s} "
            f"EMA3/5:{result.get('ema_cross_med','?'):5s}"
        )
    
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()