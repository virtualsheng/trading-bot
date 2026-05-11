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

import os
from dotenv import load_dotenv
from datetime import datetime
import pytz

from strategies.signal_engine import get_technical_signal
from notifications.emailer import send_email

load_dotenv()

SYMBOLS = ["SPY", "QQQ", "TQQQ", "SQQQ"]

def main():
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    
    if not api_key or not secret_key:
        print("❌ Missing Alpaca credentials in .env")
        return
    
    est = pytz.timezone("US/Eastern")
    now = datetime.now(est)
    
    print("=" * 60)
    print(f"TECHNICAL SIGNALS - {now.strftime('%Y-%m-%d %H:%M')} ET")
    print("=" * 60)
    
    body = []
    for symbol in SYMBOLS:
        result = get_technical_signal(symbol, api_key, secret_key)
        line = f"{symbol}: {result.get('action', 'ERROR')} | RSI: {result.get('rsi', 'N/A')}"
        print(line)
        body.append(line)
    
    header = f"Technical Signals {now.strftime('%Y-%m-%d %H:%M')} ET"
    send_email(header, "\n".join(body))

if __name__ == "__main__":
    main()