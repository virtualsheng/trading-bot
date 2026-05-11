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

# Expanded symbol list
SYMBOLS = ["SPY", "QQQ", "TQQQ", "SQQQ", "SMH", "SPMO", "EWT", "GLD", "GRID"]

def main():
    if not API_KEY or not SECRET_KEY:
        print("❌ Missing ALPACA_API_KEY or ALPACA_API_SECRET in .env")
        return

    est = pytz.timezone("US/Eastern")
    now = datetime.now(est)
    
    header = f"ORB SIGNALS {now.strftime('%Y-%m-%d %H:%M')} ET"

    print("=" * 80)
    print(header)
    print("=" * 80)

    results = []
    breakout_count = 0
    high_conviction = []

    for symbol in SYMBOLS:
        result = get_orb_signal(symbol, API_KEY, SECRET_KEY)
        
        sig = result.get("signal", "ERROR")
        curr = result.get("current", "N/A")
        high = result.get("or_high", "N/A")
        low = result.get("or_low", "N/A")
        reason = result.get("reason", "")

        # Filter low-volume / bad data
        if "insufficient data" in str(reason).lower():
            continue

        # Format line with color highlighting (console)
        if sig == "BUY":
            line = f"🚀 {symbol}: BUY     | Current={curr} | OR High={high} | OR Low={low} | {reason}"
            breakout_count += 1
            high_conviction.append(f"{symbol} (Bullish Breakout)")
        elif sig == "SELL":
            line = f"🔻 {symbol}: SELL    | Current={curr} | OR High={high} | OR Low={low} | {reason}"
            breakout_count += 1
            high_conviction.append(f"{symbol} (Bearish Breakdown)")
        else:
            line = f"   {symbol}: WAIT    | Current={curr} | OR High={high} | OR Low={low} | {reason}"

        print(line)
        results.append(line)

    # === Summary & Recommendation ===
    print("\n" + "=" * 80)
    print("SUMMARY & RECOMMENDATION")
    print("=" * 80)
    
    if breakout_count == 0:
        print("🟡 NO CLEAR BREAKOUT YET")
        print("Recommendation: Wait for price to close outside the Opening Range.")
        print("Monitor TQQQ / SQQQ closely in the next 15-30 minutes.")
    elif breakout_count == 1:
        print(f"🔥 ONE HIGH CONVICTION SIGNAL: {high_conviction[0]}")
        print("Recommendation: Consider this as your primary trade.")
    else:
        print(f"🔥 {breakout_count} BREAKOUTS DETECTED!")
        print("High Conviction Symbols:")
        for item in high_conviction:
            print(f"   • {item}")
        print("\nRecommendation: Focus on the strongest volume + conviction names.")

    # Send notifications
    body = "\n".join(results) + "\n\n" + "="*50 + "\nSUMMARY:\n" + "\n".join(high_conviction) if high_conviction else "No breakouts"
    
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