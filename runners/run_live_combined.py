"""
run_live_combined.py — Launch the Trend-Filtered ORB Strategy
──────────────────────────────────────────────────────────────
Full AI-enhanced pipeline:
  EOD Technical Signals → Bias Cache
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
        "orb_minutes":              15,
        "bar_minutes":              5,
        "risk_pct":                 0.01,
        "reward_ratio":             2.0,
        "eod_exit_time":            "15:45",
        "max_positions":            5,
        "ai_min_confidence":        0.55,
        "hold_override":            False,
        "hold_override_size":       0.5,
        # Earnings filter
        "earnings_filter_enabled":  True,
        "earnings_buffer_hours":    48,
        # Regime switching
        "regime_switching_enabled": True,
        "mean_reversion_min_conf":  0.70,
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
    print(f"  HOLD Override     : {PARAMS['hold_override']}")
    print(f"  Earnings Filter   : ±{PARAMS['earnings_buffer_hours']}h buffer")
    print(f"  Regime Switching  : {PARAMS['regime_switching_enabled']}")
    print(f"  Ollama Model      : qwen3:8b (localhost:11434)")
    print(f"  Trade Journal     : cache/trade_journal.db")
    print("=" * 70)
    print()
    print("  Daily schedule:")
    print("  • ~9:00 AM ET — Ollama warmup, earnings cache clear")
    print("  • 9:30 AM ET  — Position sync from Alpaca")
    print("  • 9:45 AM ET  — ORB / mean-reversion entries begin")
    print("  • Every 30min — Regime detection refresh")
    print("  • Noon         — No new entries after this")
    print("  • 3:45 PM ET  — Leveraged ETFs closed")
    print("  • 3:50 PM ET  — EOD signals run, bias cached for tomorrow")
    print()
    print("  Strategy switching:")
    print("  • Trending regime   → ORB momentum entries")
    print("  • Ranging regime    → Mean-reversion fade entries")
    print("  • Low liquidity     → Skip entirely")
    print("  • Earnings within 48h → Skip symbol")
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