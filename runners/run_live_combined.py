"""
run_live_combined.py — Launch the Trend-Filtered ORB Strategy v5
──────────────────────────────────────────────────────────────────
Full AI-enhanced pipeline:
  EOD Technical Signals (3:50 PM prelim + 4:15 PM final)
  Morning ORB / Mean-Reversion → AI Grader → Regime Filter → Alpaca

Switch paper/live: set ALPACA_IS_PAPER=true/false in .env
"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lumibot.brokers import Alpaca
from lumibot.traders import Trader
from strategies.trend_filtered_orb import TrendFilteredORB


def main():
    api_key    = os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_API_SECRET")
    is_paper   = os.getenv("ALPACA_IS_PAPER", "true").lower() == "true"

    if not api_key or not api_secret:
        print("❌ Missing ALPACA_API_KEY or ALPACA_API_SECRET in .env")
        return

    BROKER_CONFIG = {
        "API_KEY":    api_key,
        "API_SECRET": api_secret,
        "PAPER":      is_paper,
    }

    PARAMS = {
        "orb_minutes":        15,
        "bar_minutes":        5,
        "risk_pct":           0.01,     # 1% base risk — AI scales up/down
        "reward_ratio":       2.0,
        "eod_exit_time":      "15:45",
        "max_positions":      8,
        "ai_min_confidence":  0.55,
        "hold_override":      False,
        "hold_override_size": 0.5,
    }

    broker   = Alpaca(BROKER_CONFIG)
    strategy = TrendFilteredORB(
        broker=broker,
        parameters=PARAMS,
        name="TrendFilteredORB",
    )

    trader = Trader()
    trader.add_strategy(strategy)

    mode = "📄 PAPER TRADING" if is_paper else "💰 LIVE TRADING ⚠️ REAL MONEY"

    sentiment_url  = os.getenv("SENTIMENT_API_URL", "http://localhost:8000")
    sentiment_configured = bool(os.getenv("SENTIMENT_ADMIN_TOKEN", ""))

    print("\n" + "=" * 70)
    print("  🚀  TREND-FILTERED ORB — AI-ENHANCED LIVE STRATEGY  v5")
    print("=" * 70)
    print(f"  Mode              : {mode}")
    print(f"  Symbols           : symbols.txt ({_count_symbols()} symbols)")
    print(f"  Base Risk/Trade   : {PARAMS['risk_pct']*100:.0f}% (AI scales to 2x max)")
    print(f"  Max Positions     : {PARAMS['max_positions']}")
    print(f"  AI Min Confidence : {PARAMS['ai_min_confidence']}")
    print(f"  HOLD Override     : {PARAMS['hold_override']}")
    print(f"  Ollama Model      : qwen3:8b (localhost:11434)")
    print(f"  Trade Journal     : cache/trade_journal.db")
    print(f"  Sentiment Alpha    : {sentiment_url} ({'token set ✅' if sentiment_configured else 'no token ⚠️'})")
    print("=" * 70)
    print()
    print("  Daily schedule:")
    print("  • Script start    — Ollama warmup (model loaded immediately)")
    print("  • ~9:00 AM ET     — Earnings cache cleared, regime pre-warmed")
    print("  • 9:30 AM ET      — Position sync from Alpaca")
    print("  • 9:45 AM – noon  — ORB / mean-reversion entries")
    print("  • Every 30 min    — Regime detection refresh")
    print("  • 3:45 PM ET      — Leveraged ETFs closed")
    print("  • 3:50 PM ET      — Preliminary signals run (prelim close prices)")
    print("                      → SELL signals acted on immediately")
    print("  • 4:15 PM ET      — FINAL signals run (official closing prices)")
    print("                      → Overwrites preliminary cache")
    print("                      → Any new SELL signals acted on")
    print("  • Overnight        — Non-leveraged positions held until SELL signal")
    print()
    print("  Off-hours: bot sleeps — no API calls, no log noise")
    print("  Active window: Mon–Fri 9:30 AM – 4:25 PM ET only")
    print()
    print("  To stop: Ctrl+C")
    print("=" * 70 + "\n")

    print("  🔄 Running startup refresh (bias + earnings cache)...")
    print("     This ensures signals are current regardless of start time.")
    print()
    strategy.startup_refresh()

    trader.run_all()


def _count_symbols() -> int:
    try:
        with open("symbols.txt") as f:
            return sum(1 for l in f if l.strip() and not l.startswith("#"))
    except Exception:
        return 0


if __name__ == "__main__":
    main()