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

def load_symbols(filename="symbols.txt"):
    try:
        with open(filename, "r") as f:
            return [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        return ["SPY", "QQQ", "TQQQ", "SQQQ", "SMH"]


def main():
    if not API_KEY or not SECRET_KEY:
        print("❌ Missing ALPACA credentials")
        return

    SYMBOLS = load_symbols()
    est = pytz.timezone("US/Eastern")
    now_est = datetime.now(est)
    
    header = f"TECHNICAL SIGNALS {now_est.strftime('%Y-%m-%d %H:%M')} ET"

    print("=" * 95)
    print(header)
    print("=" * 95)

    results = []
    high_conviction = []

    for symbol in SYMBOLS:
        result = get_technical_signal(symbol, API_KEY, SECRET_KEY)
        
        action = result.get("action", "ERROR")
        rsi = result.get("rsi", "N/A")
        bull = result.get("bull_score", "N/A")
        bear = result.get("bear_score", "N/A")
        pct_chg = result.get("pct_change_open", 0)
        vol_ratio = result.get("volume_ratio", 1.0)
        rsi_int = result.get("rsi_interpretation", "")

        if action == "ERROR":
            line = f"{symbol}: ❌ ERROR"
        else:
            conviction = "🔥" if (action in ["STRONG_BUY", "STRONG_SELL"] and vol_ratio > 1.3) else "📈" if action in ["BUY", "SELL"] else "   "
            
            line = f"{conviction} {symbol}: {action:<12} | RSI={rsi} ({rsi_int}) | Chg={pct_chg:+.2f}% | Vol={vol_ratio:.2f}x | Bull={bull} Bear={bear}"
            
            if action in ["STRONG_BUY", "STRONG_SELL"] and vol_ratio > 1.3:
                high_conviction.append(f"{symbol} → {action} (Strong Volume)")

        print(line)
        results.append(line)

    # Summary
    print("\n" + "=" * 95)
    print("SUMMARY & RECOMMENDATION")
    print("=" * 95)
    
    if high_conviction:
        print("🔥 HIGH CONVICTION SIGNALS:")
        for item in high_conviction:
            print(f"   • {item}")
        print("\nRecommendation: Focus on these names for potential entries.")
    else:
        print("🟡 No high conviction signals today. Market is neutral or ranging.")

    # Send notifications
    body = "\n".join(results)
    if high_conviction:
        body += "\n\nHIGH CONVICTION:\n" + "\n".join(high_conviction)

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