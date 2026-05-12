"""
Morning ORB (Opening Range Breakout) check.
Run via cron at 9:56am EST on weekdays:
  45 9 * * 1-5 python alerts/run_orb_check.py

Checks if QQQ has broken above or below its first 15-min range.
This is the morning version of the 9:15-9:45am alerts.
"""

"""
Morning ORB Check
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


def log_signal(signal_type: str, content: str):
    """Safe logging with UTF-8 encoding"""
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_line = f"{timestamp} | {signal_type} | {content}\n"
    
    with open("logs/daily_signals.log", "a", encoding="utf-8") as f:
        f.write(log_line)


def main():
    if not API_KEY or not SECRET_KEY:
        print("❌ Missing ALPACA credentials")
        return

    SYMBOLS = load_symbols()
    est = pytz.timezone("US/Eastern")
    now = datetime.now(est)
    
    header = f"ORB SIGNALS {now.strftime('%Y-%m-%d %H:%M')} ET"

    print("=" * 110)
    print(header)
    print("=" * 110)

    results = []
    high_conviction = []

    for symbol in SYMBOLS:
        result = get_orb_signal(symbol, API_KEY, SECRET_KEY)
        
        sig = result.get("signal", "ERROR")
        curr = result.get("current", "N/A")
        or_high = result.get("or_high", "N/A")
        or_low = result.get("or_low", "N/A")
        pct_chg = result.get("pct_change_open", 0)
        vol_ratio = result.get("volume_ratio", 1.0)
        reason = result.get("reason", "")

        if "insufficient" in str(reason).lower():
            continue

        if sig == "BUY":
            conviction = "🔥" if vol_ratio > 1.5 else "🚀"
            line = f"{conviction} {symbol}: BUY     | Current={curr} | ORH={or_high} | Chg={pct_chg:+.2f}% | Vol={vol_ratio:.2f}x"
            if vol_ratio > 1.4:
                high_conviction.append(f"{symbol} → STRONG BULLISH BREAKOUT")
        elif sig == "SELL":
            conviction = "🔻" if vol_ratio > 1.5 else "📉"
            line = f"{conviction} {symbol}: SELL    | Current={curr} | ORH={or_high} | Chg={pct_chg:+.2f}% | Vol={vol_ratio:.2f}x"
            if vol_ratio > 1.4:
                high_conviction.append(f"{symbol} → STRONG BEARISH BREAKDOWN")
        else:
            line = f"   {symbol}: WAIT    | Current={curr} | ORH={or_high} | Chg={pct_chg:+.2f}% | Inside Range"

        print(line)
        results.append(line)

    log_signal("ORB", "\n".join(results))

    print("\n" + "=" * 110)
    print("SUMMARY & RECOMMENDATION")
    print("=" * 110)
    if high_conviction:
        print("🔥 HIGH CONVICTION BREAKOUTS:")
        for item in high_conviction:
            print(f"   • {item}")
    else:
        print("🟡 No breakouts yet. Monitor for decisive move outside the Opening Range.")

    body = "\n".join(results)
    if high_conviction:
        body += "\n\nHIGH CONVICTION:\n" + "\n".join(high_conviction)

    try:
        send_email(header, body)
        send_discord_message(body)
        send_telegram_message(body)
    except Exception as e:
        print(f"Notification error: {e}")


if __name__ == "__main__":
    main()