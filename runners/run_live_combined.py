"""
run_live_combined.py — Launch the Trend-Filtered ORB Strategy
──────────────────────────────────────────────────────────────
Runs the full AI-enhanced pipeline:
  EOD Technical Signals → Bias Cache
  Morning ORB → AI Grader → Regime Filter → Dynamic Sizing → Alpaca

Switch paper/live: set ALPACA_IS_PAPER=true/false in .env
"""

import os
from dotenv import load_dotenv
load_dotenv()   # Must be before lumibot imports

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
        "orb_minutes":       15,
        "bar_minutes":       5,
        "risk_pct":          0.01,     # 1% base risk — AI scales up/down
        "reward_ratio":      2.0,
        "eod_exit_time":     "15:45",
        "max_positions":     3,
        "ai_min_confidence": 0.55,     # Skip trades below this AI score
        "hold_override_size": 0.5,     # 0.5x size when bias=HOLD but ORB fires
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

    print("\n" + "=" * 70)
    print("  🚀  TREND-FILTERED ORB — AI-ENHANCED LIVE STRATEGY")
    print("=" * 70)
    print(f"  Mode              : {mode}")
    print(f"  Symbols           : symbols.txt ({_count_symbols()} symbols)")
    print(f"  Base Risk/Trade   : {PARAMS['risk_pct']*100:.0f}% (AI scales to 2x max)")
    print(f"  Max Positions     : {PARAMS['max_positions']}")
    print(f"  AI Min Confidence : {PARAMS['ai_min_confidence']}")
    print(f"  HOLD Override     : {PARAMS['hold_override_size']}x size")
    print(f"  Ollama Model      : qwen3:8b (localhost:11434)")
    print(f"  Trade Journal     : cache/trade_journal.db")
    print("=" * 70)
    print()
    print("  Daily schedule:")
    print("  • 9:30 AM ET  — ORB monitoring begins")
    print("  • 9:45 AM ET  — Earliest ORB entry (after OR established)")
    print("  • Every 30min — Regime detection refresh")
    print("  • 3:45 PM ET  — All positions closed")
    print("  • 3:50 PM ET  — EOD signals run, bias cached for tomorrow")
    print()
    print("  AI pipeline per trade:")
    print("  • Setup Grader   → confidence 0.0-1.0, size multiplier")
    print("  • Regime Detect  → market state, stop/target adjustment")
    print("  • Trade Narrator → journal entry after close")
    print()
    print("  To stop: Ctrl+C")
    print("=" * 70 + "\n")

    trader.run_all()


def _count_symbols() -> int:
    try:
        with open("symbols.txt") as f:
            return sum(1 for l in f if l.strip() and not l.startswith("#"))
    except Exception:
        return 0


if __name__ == "__main__":
    main()