"""
run_live_combined.py — Launch the Trend-Filtered ORB Strategy v6
──────────────────────────────────────────────────────────────────
Full AI-enhanced pipeline:
  EOD Technical Signals (3:50 PM prelim + 4:15 PM final)
  Morning ORB / Mean-Reversion → AI Grader → Regime Filter → Alpaca

Switch paper/live: set ALPACA_IS_PAPER=true/false in .env

Swing Mode: set SWING_MODE=true in .env for tax-efficient long-term holding.

run_live_combined.py — Launch the Trend-Filtered ORB Strategy v7
──────────────────────────────────────────────────────────────────
Full AI-enhanced pipeline:
  EOD Technical Signals (3:50 PM prelim + 4:15 PM final)
  Morning ORB / Mean-Reversion → AI Grader → Regime Filter → Alpaca

Switch paper/live: set ALPACA_IS_PAPER=true/false in .env
Swing mode:        set SWING_MODE=true in .env

Logging fix (v7):
  Only a FileHandler is added to the root logger — NOT a StreamHandler.
  LumiBot installs its own console handler at startup, so adding another
  StreamHandler causes every line to print twice. The file handler alone
  captures all output (LumiBot, strategy, ai_engine) to the log file.
"""

import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# ── File logging only (console handled by LumiBot's own handler) ──────────
# Do NOT add a StreamHandler here — LumiBot already configures one and
# adding a second causes every log line to appear twice in the console.
os.makedirs("logs", exist_ok=True)
_log_file     = f"logs/bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
_file_handler = logging.FileHandler(_log_file, encoding="utf-8")
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
)
logging.getLogger().addHandler(_file_handler)
logging.getLogger().setLevel(logging.INFO)
print(f"  Logging to: {_log_file}")

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
        "risk_pct":           0.01,     # 1% base risk per position
        "reward_ratio":       2.0,
        "eod_exit_time":      "15:45",
        "max_positions":      8,
        "ai_min_confidence":  0.55,
        "hold_override":      False,
        "hold_override_size": 0.5,

        # ── Position sizing guards ─────────────────────────────────────────
        # Minimum stop distance as % of price. Prevents absurdly large share
        # counts when the OR is very tight (e.g. flat open, <0.1% range).
        "min_stop_pct":       0.005,    # 0.5% of price minimum stop distance

        # Maximum single-position value as % of portfolio. Hard cap regardless
        # of qty calculation — prevents over-concentration in one symbol.
        "max_position_pct":   0.15,     # 15% of portfolio max per position

        # ── ORB breakout filter ────────────────────────────────────────────
        # Minimum breakout beyond the OR boundary before entry fires.
        # 0.1% filters out noise right at the OR edge.
        "min_breakout_pct":   0.001,    # 0.1% beyond OR boundary

        # ── Swing / Tax-Efficient Mode ─────────────────────────────────────
        "swing_mode":                  os.getenv("SWING_MODE", "false").lower() == "true",
        "swing_min_conviction":        75,
        "swing_sell_cooldown_days":    90,
        "swing_force_sell_conviction": 85,
        "swing_force_sell_bear_score": 5,
    }

    broker   = Alpaca(BROKER_CONFIG)
    strategy = TrendFilteredORB(
        broker=broker,
        parameters=PARAMS,
        name="TrendFilteredORB",
    )

    trader = Trader()
    trader.add_strategy(strategy)

    mode                 = "📄 PAPER TRADING" if is_paper else "💰 LIVE TRADING ⚠️ REAL MONEY"
    sentiment_url        = os.getenv("SENTIMENT_API_URL", "http://localhost:8000")
    sentiment_configured = bool(os.getenv("SENTIMENT_ADMIN_TOKEN", ""))
    swing_mode           = PARAMS["swing_mode"]

    print("\n" + "=" * 70)
    print("  🚀  TREND-FILTERED ORB — AI-ENHANCED LIVE STRATEGY  v7")
    print("=" * 70)
    print(f"  Mode              : {mode}")
    print(f"  Symbols           : symbols.txt ({_count_symbols()} symbols)")
    print(f"  Base Risk/Trade   : {PARAMS['risk_pct']*100:.0f}% (AI scales to 2x max)")
    print(f"  Max Positions     : {PARAMS['max_positions']}")
    print(f"  Max Position Size : {PARAMS['max_position_pct']*100:.0f}% of portfolio")
    print(f"  Min Stop Distance : {PARAMS['min_stop_pct']*100:.1f}% of price")
    print(f"  Min Breakout      : {PARAMS['min_breakout_pct']*100:.1f}% beyond OR")
    print(f"  AI Min Confidence : {PARAMS['ai_min_confidence']}")
    print(f"  HOLD Override     : {PARAMS['hold_override']}")
    print(f"  Swing Mode        : {'✅ ON' if swing_mode else '❌ off'}")
    if swing_mode:
        print(f"  Swing Min Conv.   : {PARAMS['swing_min_conviction']}")
        print(f"  Sell Cooldown     : {PARAMS['swing_sell_cooldown_days']}d")
        print(f"  Force-Sell Conv.  : {PARAMS['swing_force_sell_conviction']}")
        print(f"  Force-Sell Bear≥  : {PARAMS['swing_force_sell_bear_score']}")
    print(f"  Ollama Model      : llama3.2:3b (localhost:11434)")
    print(f"  Trade Journal     : cache/trade_journal.db")
    print(f"  Log File          : {_log_file}")
    print(f"  Sentiment Alpha   : {sentiment_url} ({'token set ✅' if sentiment_configured else 'no token ⚠️'})")
    print("=" * 70)
    print()
    print("  Daily schedule:")
    print("  • Script start    — Ollama warmup (model loaded immediately)")
    print("  • ~9:00 AM ET     — Earnings cache cleared, regime pre-warmed")
    print("  • 9:30 AM ET      — Position sync from Alpaca")
    print("  • 9:45 AM – noon  — ORB entries (BUY bias OR confirmed HOLD breakout)")
    print("  • Every 30 min    — Regime detection refresh")
    print("  • 3:45 PM ET      — Leveraged ETFs closed")
    print("  • 3:50 PM ET      — Preliminary signals (prelim close prices)")
    print("                      → SELL signals acted on immediately")
    print("  • 4:15 PM ET      — FINAL signals (official closing prices)")
    print("                      → Any new SELL signals acted on")
    if swing_mode:
        print("  • Overnight        — All positions held (swing mode)")
    else:
        print("  • Overnight        — Non-leveraged positions held until SELL signal")
    print()
    print("  ORB entry logic:")
    print("  • BUY/STRONG_BUY bias  → enter on confirmed breakout above OR High")
    print("  • HOLD bias            → enter on confirmed breakout above OR High (0.5x size)")
    print("  • SELL/STRONG_SELL     → entry blocked for LONG; SHORT only (leveraged pair)")
    print("  • No entry if price is inside the opening range (WAIT state)")
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