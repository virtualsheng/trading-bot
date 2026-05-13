"""
End-of-day technical signal check.
Run via cron at 3:50pm EST on weekdays:
  50 15 * * 1-5 python alerts/run_technical_signals.py

Reads daily bars, computes EMA/RSI/MACD signals,
and prints a clear BUY/SELL/HOLD for each symbol.
This is the EOD version of the 3:50-4:15pm alerts.
Primary bias for next day's trading decisions
"""

import os
import sys
from dotenv import load_dotenv
from datetime import datetime
import pytz
import json

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


def log_signal(signal_type: str, content: str):
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open("logs/daily_signals.log", "a", encoding="utf-8") as f:
        f.write(f"{timestamp} | {signal_type} | {content}\n")


def main():
    if not API_KEY or not SECRET_KEY:
        print("❌ Missing ALPACA credentials")
        return

    SYMBOLS = load_symbols()
    est = pytz.timezone("US/Eastern")
    now_est = datetime.now(est)
    header = f"TECHNICAL SIGNALS {now_est.strftime('%Y-%m-%d %H:%M')} ET"

    print("=" * 120)
    print(header)
    print("=" * 120)

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
        rsi_int = result.get("rsi_interpretation", "Neutral")
        above_200 = result.get("above_sma200", False)

        if action == "ERROR":
            line = f"{symbol}: ❌ ERROR"
        else:
            prefix = "🔥" if (action in ["STRONG_BUY", "STRONG_SELL"] and vol_ratio > 1.3) else \
                     "🚀" if action == "BUY" else \
                     "🔻" if action == "SELL" else "   "

            line = (
                f"{prefix} {symbol:<6}: {action:<12} | "
                f"RSI={rsi} ({rsi_int}) | "
                f"Chg={pct_chg:+.2f}% | "
                f"Vol={vol_ratio:.2f}x | "
                f"Bull={bull} Bear={bear} | Above200={above_200}"
            )

            if action in ["STRONG_BUY", "STRONG_SELL"] and vol_ratio > 1.3:
                high_conviction.append(f"{symbol} → {action} (Strong Volume + Momentum)")

        print(line)
        results.append(line)

    log_signal("TECHNICAL", "\n".join(results))

    print("\n" + "=" * 120)
    print("SUMMARY & RECOMMENDATION")
    print("=" * 120)

    # Market Context using SPY/QQQ
    spy_result = next((get_technical_signal(s, API_KEY, SECRET_KEY) for s in ["SPY", "QQQ"] if s in SYMBOLS), {})
    spy_rsi = spy_result.get("rsi", 50)

    if spy_rsi > 70:
        market_tone = "⚠️  Broad market is OVERBOUGHT (RSI > 70). Caution on new longs."
    elif spy_rsi < 35:
        market_tone = "🟢 Broad market is OVERSOLD (RSI < 35). Potential opportunity."
    else:
        market_tone = "🟡 Broad market is NEUTRAL."

    print(f"Market Context: {market_tone}")
    if high_conviction:
        print("\n🔥 HIGH CONVICTION SIGNALS:")
        for item in high_conviction:
            print(f"   • {item}")
    else:
        print("\n🟡 No high conviction signals today.")

    body = "\n".join(results)
    if high_conviction:
        body += "\n\nHIGH CONVICTION:\n" + "\n".join(high_conviction)

    # Write bias cache so live bot can use these signals tomorrow
    bias = {}
    for symbol in SYMBOLS:
        r = get_technical_signal(symbol, API_KEY, SECRET_KEY)
        bias[symbol] = {
            "action":     r.get("action", "HOLD"),
            "bull_score": r.get("bull_score", 0),
            "bear_score": r.get("bear_score", 0),
            "rsi":        r.get("rsi", 50),
            "vol_ratio":  r.get("volume_ratio", 1.0),
            "date":       now_est.strftime("%Y-%m-%d"),
        }
    os.makedirs("cache", exist_ok=True)
    with open("cache/daily_bias.json", "w") as f:
        json.dump(bias, f, indent=2)
    print("\n✅ Bias cache written to cache/daily_bias.json")

    try:
        send_email(header, body)
        send_discord_message(body)
        send_telegram_message(body)
    except Exception as e:
        print(f"Notification error: {e}")


if __name__ == "__main__":
    main()