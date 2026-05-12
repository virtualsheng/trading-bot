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
    try:
        with open(filename, "r") as f:
            return [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        return ["SPY", "QQQ", "TQQQ", "SQQQ", "SMH"]

def log_signal(signal_type, content):
    os.makedirs("logs", exist_ok=True)
    with open("logs/daily_signals.log", "a") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {signal_type} | {content}\n")

def main():
    if not API_KEY or not SECRET_KEY:
        print("❌ Missing ALPACA credentials")
        return

    SYMBOLS = load_symbols()
    est = pytz.timezone("US/Eastern")
    now = datetime.now(est)
    
    header = f"ORB SIGNALS {now.strftime('%Y-%m-%d %H:%M')} ET"

    print("=" * 100)
    print(header)
    print("=" * 100)

    results = []
    high_conviction = []

    for symbol in SYMBOLS:
        result = get_orb_signal(symbol, API_KEY, SECRET_KEY)
        
        sig = result.get("signal", "ERROR")
        curr = result.get("current", "N/A")
        or_high = result.get("or_high", "N/A")
        or_low = result.get("or_low", "N/A")
        reason = result.get("reason", "")
        pct_chg = result.get("pct_change_open", 0)      # Added
        vol_ratio = result.get("volume_ratio", 1.0)     # Added

        if "insufficient data" in str(reason).lower():
            continue

        if sig == "BUY":
            conviction = "🔥" if vol_ratio > 1.5 else "🚀"
            line = f"{conviction} {symbol}: BUY     | Current={curr} | ORH={or_high} | ORL={or_low} | Chg={pct_chg:+.2f}% | Vol={vol_ratio:.2f}x"
            if vol_ratio > 1.4:
                high_conviction.append(f"{symbol} → STRONG BULLISH BREAKOUT (Vol {vol_ratio:.2f}x)")
        elif sig == "SELL":
            conviction = "🔻" if vol_ratio > 1.5 else "📉"
            line = f"{conviction} {symbol}: SELL    | Current={curr} | ORH={or_high} | ORL={or_low} | Chg={pct_chg:+.2f}% | Vol={vol_ratio:.2f}x"
            if vol_ratio > 1.4:
                high_conviction.append(f"{symbol} → STRONG BEARISH BREAKDOWN (Vol {vol_ratio:.2f}x)")
        else:
            line = f"   {symbol}: WAIT    | Current={curr} | ORH={or_high} | ORL={or_low} | Chg={pct_chg:+.2f}% | Inside Range"

        print(line)
        results.append(line)

    log_signal("ORB", "\n".join(results))

    # === Summary & Recommendation ===
    print("\n" + "=" * 100)
    print("SUMMARY & HIGH CONVICTION SIGNALS")
    print("=" * 100)
    
    if high_conviction:
        print("🔥 HIGH CONVICTION BREAKOUTS:")
        for item in high_conviction:
            print(f"   • {item}")
        print("\nRecommendation: Prioritize these names. Strong volume + clean breakout = highest probability.")
    else:
        print("🟡 NO CLEAR BREAKOUTS YET")
        print("Recommendation: Wait for a decisive 5-min close outside the Opening Range.")

    # Send notifications
    body = "\n".join(results)
    if high_conviction:
        body += "\n\nHIGH CONVICTION BREAKOUTS:\n" + "\n".join(high_conviction)

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