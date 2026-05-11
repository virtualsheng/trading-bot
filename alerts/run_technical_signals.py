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

from notifications.emailer import send_email
from notifications.discord import send_discord_message
from notifications.telegram import send_telegram_message

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

SYMBOLS = [
    "SPY",
    "QQQ",
    "SMH",
    "TQQQ",
    "SQQQ"
]

def main():

    results = []

    header = (
        f"TECHNICAL SIGNALS "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    print("=" * 60)
    print(header)
    print("=" * 60)

    for symbol in SYMBOLS:

        result = get_technical_signal(
            symbol,
            API_KEY,
            SECRET_KEY
        )

        line = (
            f"{symbol}: "
            f"{result.get('action')} | "
            f"RSI={result.get('rsi')} | "
            f"Bull={result.get('bull_score')} | "
            f"Bear={result.get('bear_score')}"
        )

        print(line)

        results.append(line)

    body = "\n".join(results)

    send_email(header, body)
    send_discord_message(body)
    send_telegram_message(body)

if __name__ == "__main__":
    main()
