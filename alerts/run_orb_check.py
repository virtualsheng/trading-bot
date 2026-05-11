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

from core.orb import get_orb_signal

from notifications.emailer import send_email
from notifications.discord import send_discord_message
from notifications.telegram import send_telegram_message

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

SYMBOLS = [
    "SPY",
    "QQQ",
    "TQQQ",
    "SQQQ"
]

def main():

    results = []

    header = (
        f"ORB SIGNALS "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    print("=" * 60)
    print(header)
    print("=" * 60)

    for symbol in SYMBOLS:

        result = get_orb_signal(
            symbol,
            API_KEY,
            SECRET_KEY
        )

        line = (
            f"{symbol}: "
            f"{result.get('signal')} | "
            f"Current={result.get('current')} | "
            f"OR High={result.get('or_high')} | "
            f"OR Low={result.get('or_low')}"
        )

        print(line)

        results.append(line)

    body = "\n".join(results)

    send_email(header, body)
    send_discord_message(body)
    send_telegram_message(body)

if __name__ == "__main__":
    main()
