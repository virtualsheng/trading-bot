"""
End-of-day technical signal check.
Run via cron at 3:50pm EST on weekdays:
  50 15 * * 1-5 python alerts/run_technical_signals.py

Reads daily bars, computes EMA/RSI/MACD signals,
and prints a clear BUY/SELL/HOLD for each symbol.
This is the EOD version of the 3:50-4:15pm alerts.
"""

import os
import sys
from dotenv import load_dotenv
from datetime import datetime
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.signal_engine import get_technical_signal
from notifications.emailer import send_email
from notifications.discord import send_discord_message
from notifications.telegram import send_telegram_message

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_API_SECRET")

SYMBOLS = ["SPY", "QQQ", "TQQQ", "SQQQ", "DRAM", "SMH", "SPMO", "EWT", "DBMF", "GLD", "GRID"]   # Added More Symbols

def main():
    if not API_KEY or not SECRET_KEY:
        print("❌ Missing ALPACA_API_KEY or ALPACA_API_SECRET in .env")
        return

    est = pytz.timezone("US/Eastern")
    now_est = datetime.now(est)
    
    header = f"TECHNICAL SIGNALS {now_est.strftime('%Y-%m-%d %H:%M')} ET"

    print("=" * 75)
    print(header)
    print("=" * 75)

    results = []

    for symbol in SYMBOLS:
        try:
            result = get_technical_signal(symbol, API_KEY, SECRET_KEY)
            
            action = result.get("action", "ERROR")
            rsi = result.get("rsi", "N/A")
            bull = result.get("bull_score", "N/A")
            bear = result.get("bear_score", "N/A")
            error = result.get("error", "")

            if error:
                line = f"{symbol}: ❌ ERROR - {error[:80]}"
            else:
                line = f"{symbol}: {action:<12} | RSI={rsi} | Bull={bull} | Bear={bear}"
            
            print(line)
            results.append(line)
            
        except Exception as e:
            error_line = f"{symbol}: ❌ ERROR - {str(e)[:80]}"
            print(error_line)
            results.append(error_line)

    body = "\n".join(results)
    
    try:
        send_email(header, body)
        print("\n✅ Email sent")
    except Exception as e:
        print(f"⚠️ Email failed: {e}")

    try:
        send_discord_message(body)
        print("✅ Discord sent")
    except Exception as e:
        print(f"⚠️ Discord failed: {e}")

    try:
        send_telegram_message(body)
        print("✅ Telegram sent")
    except Exception as e:
        print(f"⚠️ Telegram failed: {e}")


if __name__ == "__main__":
    main()