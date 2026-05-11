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

API_KEY    = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_API_SECRET")


def load_symbols(filename="symbols.txt"):
    try:
        with open(filename, "r") as f:
            return [
                line.strip().upper()
                for line in f
                if line.strip() and not line.startswith("#")
            ]
    except FileNotFoundError:
        return ["SPY", "QQQ", "TQQQ", "SQQQ", "SMH"]


def classify_conviction(action, bull_score, bear_score, vol_ratio, rsi):
    """
    Derive conviction tier from the actual signal_engine output fields.
    signal_engine returns BUY/SELL/HOLD — never STRONG_BUY/STRONG_SELL.
    We compute conviction here from the supporting evidence.

    HIGH conviction BUY:  action=BUY  + bull_score >= 5 + vol_ratio >= 1.3
    HIGH conviction SELL: action=SELL + bear_score >= 5 + vol_ratio >= 1.3
    MODERATE BUY/SELL:    action=BUY/SELL without full volume confirmation
    LOW:                  HOLD
    """
    if action == "BUY":
        if isinstance(bull_score, int) and bull_score >= 5 and vol_ratio >= 1.3:
            return "HIGH"
        return "MODERATE"
    elif action == "SELL":
        if isinstance(bear_score, int) and bear_score >= 5 and vol_ratio >= 1.3:
            return "HIGH"
        return "MODERATE"
    return "LOW"


def main():
    if not API_KEY or not SECRET_KEY:
        print("❌ Missing ALPACA credentials in .env")
        return

    SYMBOLS = load_symbols()
    est     = pytz.timezone("US/Eastern")
    now_est = datetime.now(est)
    header  = f"TECHNICAL SIGNALS {now_est.strftime('%Y-%m-%d %H:%M')} ET"

    print("=" * 95)
    print(header)
    print("=" * 95)

    results          = []   # raw lines for notifications
    buys             = []   # (symbol, conviction, vol_ratio, bull_score, rsi)
    sells            = []
    high_conv_buys   = []
    high_conv_sells  = []
    errors           = []

    for symbol in SYMBOLS:
        result = get_technical_signal(symbol, API_KEY, SECRET_KEY)

        action     = result.get("action", "ERROR")
        rsi        = result.get("rsi", "N/A")
        rsi_int    = result.get("rsi_interpretation", "")
        bull       = result.get("bull_score", "N/A")
        bear       = result.get("bear_score", "N/A")
        pct_chg    = result.get("pct_change_open", 0) or 0
        vol_ratio  = result.get("volume_ratio", 1.0) or 1.0
        error_msg  = result.get("error", None)

        if action == "ERROR" or error_msg:
            reason = error_msg or "unknown error"
            line   = f"    {symbol}: WAIT         | {reason[:60]}"
            errors.append(symbol)
        else:
            conviction = classify_conviction(action, bull, bear, vol_ratio, rsi)

            # Emoji prefix
            if action == "BUY" and conviction == "HIGH":
                prefix = "🔥"
            elif action == "BUY":
                prefix = "📈"
            elif action == "SELL" and conviction == "HIGH":
                prefix = "🔻"
            elif action == "SELL":
                prefix = "📉"
            else:
                prefix = "   "

            line = (
                f"{prefix} {symbol:<6}: {action:<12} | "
                f"RSI={rsi} ({rsi_int:<10}) | "
                f"Chg={pct_chg:+.2f}% | "
                f"Vol={vol_ratio:.2f}x | "
                f"Bull={bull} Bear={bear}"
            )

            # Bucket results for summary
            if action == "BUY":
                buys.append((symbol, conviction, vol_ratio, bull, rsi))
                if conviction == "HIGH":
                    high_conv_buys.append(
                        f"{symbol} — Bull:{bull}/6, Vol:{vol_ratio:.2f}x, RSI:{rsi}"
                    )
            elif action == "SELL":
                sells.append((symbol, conviction, vol_ratio, bear, rsi))
                if conviction == "HIGH":
                    high_conv_sells.append(
                        f"{symbol} — Bear:{bear}/6, Vol:{vol_ratio:.2f}x, RSI:{rsi}"
                    )

        print(line)
        results.append(line)

    # ── Summary ────────────────────────────────────────────────────────────
    sep = "=" * 95
    print(f"\n{sep}")
    print("SUMMARY & RECOMMENDATION")
    print(sep)

    # Overall market tone from SPY/QQQ
    spy_result = next((r for r in [
        get_technical_signal(s, API_KEY, SECRET_KEY)
        for s in ["SPY", "QQQ"] if s in SYMBOLS
    ]), {})
    spy_action = spy_result.get("action", "HOLD")
    spy_rsi    = spy_result.get("rsi", 50) or 50

    if spy_rsi > 70:
        market_tone = "⚠️  Broad market is OVERBOUGHT (SPY/QQQ RSI > 70). New longs carry elevated risk."
    elif spy_rsi < 35:
        market_tone = "🟢 Broad market is OVERSOLD (SPY/QQQ RSI < 35). Potential mean-reversion opportunity."
    else:
        market_tone = "🟡 Broad market is NEUTRAL. Sector rotation may be driving individual signals."

    print(f"\nMarket Context: {market_tone}")
    print(f"Total Signals  : {len(SYMBOLS)} symbols scanned")
    print(f"BUY signals    : {len(buys)}  ({len(high_conv_buys)} high conviction)")
    print(f"SELL signals   : {len(sells)}  ({len(high_conv_sells)} high conviction)")
    print(f"HOLD           : {len(SYMBOLS) - len(buys) - len(sells) - len(errors)}")
    if errors:
        print(f"Errors/No data : {len(errors)} ({', '.join(errors)})")

    # High conviction details
    summary_lines = []
    if high_conv_buys:
        print(f"\n🔥 HIGH CONVICTION BUYS ({len(high_conv_buys)}):")
        for item in high_conv_buys:
            print(f"   • {item}")
            summary_lines.append(f"🔥 BUY  {item}")

    if high_conv_sells:
        print(f"\n🔻 HIGH CONVICTION SELLS ({len(high_conv_sells)}):")
        for item in high_conv_sells:
            print(f"   • {item}")
            summary_lines.append(f"🔻 SELL {item}")

    # Moderate signals — still worth knowing
    mod_buys  = [(s, v, b) for s, c, v, b, r in buys  if c == "MODERATE"]
    mod_sells = [(s, v, b) for s, c, v, b, r in sells if c == "MODERATE"]

    if mod_buys:
        names = ", ".join(s for s, _, _ in mod_buys)
        print(f"\n📈 Moderate BUY signals: {names}")
        summary_lines.append(f"📈 Moderate buys: {names}")

    if mod_sells:
        names = ", ".join(s for s, _, _ in mod_sells)
        print(f"📉 Moderate SELL signals: {names}")
        summary_lines.append(f"📉 Moderate sells: {names}")

    # Actionable recommendation
    print(f"\n{'─'*60}")
    if high_conv_buys and spy_rsi < 75:
        rec = (
            f"✅ ACTIONABLE: {len(high_conv_buys)} high-conviction buy(s) with trend + volume "
            f"confirmation. Consider entries on: {', '.join(s.split(' —')[0] for s in high_conv_buys)}"
        )
    elif high_conv_sells and spy_rsi > 25:
        rec = (
            f"⚠️  ACTIONABLE: {len(high_conv_sells)} high-conviction sell(s). "
            f"Consider reducing exposure to: {', '.join(s.split(' —')[0] for s in high_conv_sells)}"
        )
    elif len(buys) >= 4 and spy_rsi < 70:
        rec = (
            f"🟢 CONSTRUCTIVE: {len(buys)} buy signals but none high-conviction yet. "
            f"Watch for volume confirmation before entering."
        )
    elif spy_rsi > 80:
        rec = (
            "🔴 CAUTION: Market is extremely overbought (RSI > 80). "
            "Avoid chasing. Wait for pullback or RSI reset below 70."
        )
    else:
        rec = "🟡 NEUTRAL: No clear actionable setups today. Stay patient."

    print(rec)
    print(f"{'─'*60}\n")
    summary_lines.append(rec)

    # ── Notifications ──────────────────────────────────────────────────────
    full_body = (
        f"{header}\n\n"
        + "\n".join(results)
        + f"\n\n{'='*60}\nSUMMARY\n{'='*60}\n"
        + f"{market_tone}\n"
        + f"BUY: {len(buys)} ({len(high_conv_buys)} high conv) | "
        + f"SELL: {len(sells)} ({len(high_conv_sells)} high conv)\n\n"
        + "\n".join(summary_lines)
    )

    try:
        send_email(header, full_body)
        print("✅ Email sent")
    except Exception as e:
        print(f"⚠️  Email failed: {e}")

    try:
        send_discord_message(full_body)
        print("✅ Discord sent")
    except Exception as e:
        print(f"⚠️  Discord failed: {e}")

    try:
        send_telegram_message(full_body)
        print("✅ Telegram sent")
    except Exception as e:
        print(f"⚠️  Telegram failed: {e}")


if __name__ == "__main__":
    main()