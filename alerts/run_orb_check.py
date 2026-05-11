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
import pytz

from core.orb import get_orb_signal

from notifications.emailer import send_email
from notifications.discord import send_discord_message
from notifications.telegram import send_telegram_message

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_API_SECRET")

SYMBOLS = ["SPY", "QQQ", "TQQQ", "SQQQ", "DRAM", "SMH", "SPMO", "EWT", "DBMF", "GLD", "GRID"]   # Added More Symbols

def main():
    if not API_KEY or not SECRET_KEY:
        print("❌ Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
        return

    est = pytz.timezone("US/Eastern")
    now_est = datetime.now(est)
    
    header = f"ORB SIGNALS {now_est.strftime('%Y-%m-%d %H:%M')} ET"

    print("=" * 70)
    print(header)
    print("=" * 70)

    results = []

    for symbol in SYMBOLS:
        result = get_orb_signal(symbol, API_KEY, SECRET_KEY)
        
        sig = result.get("signal", "ERROR")
        curr = result.get("current", "N/A")
        high = result.get("or_high", "N/A")
        low = result.get("or_low", "N/A")
        reason = result.get("reason", "")

        line = f"{symbol}: {sig} | Current={curr} | OR High={high} | OR Low={low} | {reason}"
        print(line)
        results.append(line)

    body = "\n".join(results)

    try:
        send_email(header, body)
        print("✅ Email sent")
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