"""
Morning ORB (Opening Range Breakout) check.
Run via cron at 9:45am EST on weekdays:
  45 9 * * 1-5 python alerts/run_orb_check.py

Checks if QQQ has broken above or below its first 15-min range.
This is the morning version of the 9:15-9:45am alerts.
"""

import os
import sys
from dotenv import load_dotenv
from datetime import datetime
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orb import get_orb_signal
from notifications.emailer import send_email
from notifications.discord import send_discord_message
from notifications.telegram import send_telegram_message

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_API_SECRET")

def load_symbols(filename="symbols.txt"):
    """Load symbols from file, ignore empty lines and comments"""
    try:
        with open(filename, "r") as f:
            symbols = []
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    symbols.append(line.upper())
        return symbols
    except FileNotFoundError:
        print(f"⚠️  {filename} not found. Using default symbols.")
        return ["SPY", "QQQ", "TQQQ", "SQQQ", "SMH"]

def main():
    if not API_KEY or not SECRET_KEY:
        print("❌ Missing ALPACA_API_KEY or ALPACA_API_SECRET in .env")
        return

    SYMBOLS = load_symbols()

    est = pytz.timezone("US/Eastern")
    now = datetime.now(est)
    
    header = f"ORB SIGNALS {now.strftime('%Y-%m-%d %H:%M')} ET"

    print("=" * 85)
    print(header)
    print("=" * 85)

    results = []
    breakouts = []

    for symbol in SYMBOLS:
        result = get_orb_signal(symbol, API_KEY, SECRET_KEY)
        
        sig = result.get("signal", "ERROR")
        curr = result.get("current", "N/A")
        high = result.get("or_high", "N/A")
        low = result.get("or_low", "N/A")
        reason = result.get("reason", "")

        if "insufficient data" in str(reason).lower():
            continue

        if sig == "BUY":
            line = f"🚀 {symbol}: BUY     | Current={curr} | ORH={high} | ORL={low}"
            breakouts.append(f"🚀 {symbol} - Bullish Breakout")
        elif sig == "SELL":
            line = f"🔻 {symbol}: SELL    | Current={curr} | ORH={high} | ORL={low}"
            breakouts.append(f"🔻 {symbol} - Bearish Breakdown")
        else:
            line = f"   {symbol}: WAIT    | Current={curr} | ORH={high} | ORL={low} | Inside Range"

        print(line)
        results.append(line)

    # === Summary ===
    print("\n" + "=" * 85)
    print("SUMMARY & HIGH CONVICTION SIGNALS")
    print("=" * 85)
    
    if not breakouts:
        print("🟡 NO BREAKOUTS YET")
        print("→ Wait for price to break and close outside the Opening Range.")
    else:
        print("🔥 BREAKOUT SIGNALS DETECTED:")
        for b in breakouts:
            print(f"   {b}")

    # Send notifications
    body = "\n".join(results)
    if breakouts:
        body += "\n\nHIGH CONVICTION:\n" + "\n".join(breakouts)

    try:
        send_email(header, body)
        print("\n✅ Email sent")
    except: pass
    try:
        send_discord_message(body)
        print("✅ Discord sent")
    except: pass
    try:
        send_telegram_message(body)
        print("✅ Telegram sent")
    except: pass


if __name__ == "__main__":
    main()