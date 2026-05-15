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

run_live_combined.py — Launch the Trend-Filtered ORB Strategy v8
──────────────────────────────────────────────────────────────────
v8 changes:
  - FINAL EOD signals via after_market_closes() lifecycle hook
  - File logging only (no duplicate StreamHandler)
  - Sentiment URL corrected to /api/v1/analyze

run_live_combined.py — Launch the Trend-Filtered ORB Strategy v9
──────────────────────────────────────────────────────────────────
v9 changes:
  - sleeptime_orb = "2M" — 2-min iterations during 9:45 AM–noon ORB window
  - sleeptime_default = "5M" — 5-min iterations outside ORB window
  - after_market_closes delay = 5 min — waits for official close prices to settle
  - All order direction bugs fixed (always BUY, never short-sell)
  - File logging only (no duplicate StreamHandler)
"""

import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# ── File logging only — LumiBot's own StreamHandler handles console ───────────
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
        # ── Iteration speed ───────────────────────────────────────────────
        # 2-min iterations during the ORB entry window (9:45 AM – noon)
        # 5-min iterations the rest of the day
        "sleeptime_orb":     "2M",
        "sleeptime_default": "5M",

        # ── EOD signal delay ──────────────────────────────────────────────
        # after_market_closes() waits this many minutes before running FINAL
        # signals. Official closing prices on Alpaca can lag 1-3 min after
        # 4:00 PM — waiting 5 min ensures prices are settled.
        "after_close_delay_minutes": 5,

        # ── Strategy ──────────────────────────────────────────────────────
        "orb_minutes":        15,
        "bar_minutes":        5,
        "risk_pct":           0.01,
        "reward_ratio":       2.0,
        "eod_exit_time":      "15:45",
        "max_positions":      8,
        "ai_min_confidence":  0.55,
        "hold_override":      False,
        "hold_override_size": 0.5,
        "min_stop_pct":       0.005,
        "max_position_pct":   0.15,
        "min_breakout_pct":   0.001,
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
    sentiment_base       = os.getenv("SENTIMENT_API_URL", "http://localhost:8000")
    sentiment_configured = bool(os.getenv("SENTIMENT_ADMIN_TOKEN", ""))
    swing_mode           = PARAMS["swing_mode"]

    print("\n" + "=" * 70)
    print("  🚀  TREND-FILTERED ORB — AI-ENHANCED LIVE STRATEGY  v9")
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
    print(f"  ORB Iteration     : {PARAMS['sleeptime_orb']} (9:45 AM–noon)")
    print(f"  Off-ORB Iteration : {PARAMS['sleeptime_default']}")
    print(f"  After-Close Delay : {PARAMS['after_close_delay_minutes']} min (waits for close prices)")
    print(f"  Ollama Model      : llama3.2:3b (localhost:11434)")
    print(f"  Trade Journal     : cache/trade_journal.db")
    print(f"  Log File          : {_log_file}")
    print(f"  Sentiment Alpha   : {sentiment_base}/api/v1/analyze "
          f"({'token set ✅' if sentiment_configured else 'no token ⚠️'})")
    print("=" * 70)
    print()
    print("  Trade model: ALWAYS BUY — never short-sell")
    print("  • BUY signal  → BUY bull leveraged ETF (e.g. QQQ→TQQQ)")
    print("  • SELL signal → BUY inverse ETF (e.g. IBIT→BITI) if one exists")
    print("  • SELL signal + no inverse ETF → skip (e.g. RKLB, URA)")
    print("  • HOLD signal → BUY bull ETF only on upside breakout (0.5× size)")
    print()
    print("  Daily schedule:")
    print("  • Script start    — Ollama warmup (model loaded immediately)")
    print("  • ~9:00 AM ET     — Earnings cache cleared, regime pre-warmed")
    print("  • 9:30 AM ET      — Position sync from Alpaca")
    print("  • 9:45 AM – noon  — ORB entries (2-min iterations)")
    print("  • Noon – 3:45 PM  — Monitor only (5-min iterations)")
    print("  • 3:45 PM ET      — Leveraged/inverse ETFs closed")
    print("  • 3:50 PM ET      — PRELIM signals → SELL signals acted on")
    print("  • ~4:05 PM ET     — FINAL signals via after_market_closes()")
    print("                      (5-min delay for closing prices to settle)")
    if swing_mode:
        print("  • Overnight        — All direct-trade positions held")
    else:
        print("  • Overnight        — Direct-trade positions held until SELL signal")
    print()
    print("  To stop: Ctrl+C")
    print("=" * 70 + "\n")

    print("  🔄 Running startup refresh (bias + earnings cache)...")
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