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
    now_est = datetime.now(est)
    
    header = f"TECHNICAL SIGNALS {now_est.strftime('%Y-%m-%d %H:%M')} ET"

    print("=" * 85)
    print(header)
    print("=" * 85)

    results = []
    strong_signals = []

    for symbol in SYMBOLS:
        try:
            result = get_technical_signal(symbol, API_KEY, SECRET_KEY)
            
            action = result.get("action", "ERROR")
            rsi = result.get("rsi", "N/A")
            bull = result.get("bull_score", "N/A")
            bear = result.get("bear_score", "N/A")
            error = result.get("error", "")

            if error:
                line = f"{symbol}: ❌ ERROR - {error[:70]}"
            else:
                if action in ["STRONG_BUY", "STRONG_SELL"]:
                    line = f"🔥 {symbol}: {action:<12} | RSI={rsi} | Bull={bull} | Bear={bear}"
                    strong_signals.append(f"{symbol}: {action} (High Conviction)")
                elif action in ["BUY", "SELL"]:
                    line = f"📈 {symbol}: {action:<12} | RSI={rsi} | Bull={bull} | Bear={bear}"
                else:
                    line = f"   {symbol}: {action:<12} | RSI={rsi} | Bull={bull} | Bear={bear}"

            print(line)
            results.append(line)
            
        except Exception as e:
            error_line = f"{symbol}: ❌ ERROR - {str(e)[:80]}"
            print(error_line)
            results.append(error_line)

    # === Summary & High Conviction ===
    print("\n" + "=" * 85)
    print("SUMMARY & HIGH CONVICTION SIGNALS")
    print("=" * 85)
    
    if strong_signals:
        print("🔥 HIGH CONVICTION SIGNALS:")
        for s in strong_signals:
            print(f"   • {s}")
    else:
        print("🟡 No strong signals today. Mostly HOLD/BUY/SELL signals.")

    # Send notifications
    body = "\n".join(results)
    if strong_signals:
        body += "\n\nHIGH CONVICTION SIGNALS:\n" + "\n".join(strong_signals)

    try:
        send_email(header, body)
        print("\n✅ Email sent")
    except Exception as e:
        print(f"⚠️ Email failed: {e}")

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