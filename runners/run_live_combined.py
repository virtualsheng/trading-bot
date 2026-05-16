"""
run_live_combined.py — Trend-Filtered ORB Strategy v17
────────────────────────────────────────────────────────
Unified launcher for both ORB intraday and Swing mode.
Configured by default for a $2,000 cash account trading QQQ only.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ORB MODE (default, SWING_MODE=false in .env)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Signal: QQQ only (symbols.txt should contain just QQQ)
  Execution: TQQQ (BUY signal) or SQQQ (SELL signal)
  1 trade per day max — re-entry blocked after STOP or target passed
  No hard target exit — 2% trailing stop + EOD close handles all exits
  Stop arms 15 min after entry (protects against early wicks)
  Stop placed at OR low (textbook ORB placement)
  Account type: CASH — PDT rule does not apply
  (Cash accounts settle T+1; fine for 1 trade/day)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SWING MODE (SWING_MODE=true in .env)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Signal: all symbols in symbols.txt ranked by conviction
  Up to 3 new BUY entries per day (highest conviction first)
  Max 10 positions held concurrently at any time
  Trades underlying ETF directly (no leverage)
  Holds for weeks/months — sell on SELL signal or cooldown
  30% of portfolio per position ($600 on $2k account)
  target_exit=True for swing — takes defined profit at target

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ACCOUNT NOTES ($2,000 cash account)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Use Alpaca CASH account type — not margin
  PDT rule requires $25k+ on margin accounts to make >3 day trades
  per rolling 5-day window; cash accounts are exempt entirely
  ORB:   2% risk = $40/trade, 40% cap = $800 max (~24 TQQQ shares)
  Swing: 30% cap = $600/position, up to 3 new positions/day

Switch paper/live: set ALPACA_IS_PAPER=true/false in .env
Swing mode:        set SWING_MODE=true in .env
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
    swing_mode = os.getenv("SWING_MODE", "false").lower() == "true"

    if not api_key or not api_secret:
        print("❌ Missing ALPACA_API_KEY or ALPACA_API_SECRET in .env")
        return

    BROKER_CONFIG = {
        "API_KEY":    api_key,
        "API_SECRET": api_secret,
        "PAPER":      is_paper,
    }

    if swing_mode:
        # ── Swing mode: multi-symbol, long-hold, $2k account ─────────────
        PARAMS = {
            "sleeptime_orb":             "2M",
            "sleeptime_default":         "5M",
            "after_close_delay_minutes": 5,
            "orb_minutes":               15,
            "bar_minutes":               5,
            "risk_pct":                  0.02,
            "reward_ratio":              2.0,
            "eod_exit_time":             "15:45",
            # Swing: up to 10 concurrent positions, 3 new entries per day max.
            # 30% cap = $600 per position on a $2k account.
            "max_positions":             10,
            "max_position_pct":          0.30,
            "min_stop_pct":              0.005,
            "min_breakout_pct":          0.001,
            "ai_min_confidence":         0.55,
            "hold_override":             False,
            "hold_override_size":        0.5,
            "stop_mode":                 "or_low",
            "stop_delay_minutes":        15,
            # Swing exit: close at target (defined profit)
            "target_exit":               True,
            "target_scale_out":          1.0,
            "trail_stop_pct":            0.02,
            # Swing mode settings
            "swing_mode":                True,
            "swing_min_conviction":      70,
            "swing_sell_cooldown_days":  60,
            "swing_force_sell_conviction": 85,
            "swing_force_sell_bear_score": 5,
        }
    else:
        # ── ORB mode: single symbol (QQQ), $2k cash account ──────────────
        PARAMS = {
            "sleeptime_orb":             "2M",
            "sleeptime_default":         "5M",
            "after_close_delay_minutes": 5,
            "orb_minutes":               15,
            "bar_minutes":               5,
            "risk_pct":                  0.02,   # 2% risk = $40 on $2k account
            "reward_ratio":              2.0,    # 2:1 reference only (not a hard exit)
            "eod_exit_time":             "15:45",
            # ORB: 1 position max (QQQ only), 40% cap = $800 max position.
            # Cash account: PDT rule does not apply. T+1 settlement is fine
            # for 1 trade per day (funds available again next morning).
            "max_positions":             1,
            "max_position_pct":          0.40,   # 40% = ~$800 (~24 TQQQ shares at $33)
            "min_stop_pct":              0.005,  # floor, scaled x3 = 1.5% for TQQQ/SQQQ
            "min_breakout_pct":          0.001,
            "ai_min_confidence":         0.55,
            "hold_override":             False,
            "hold_override_size":        0.5,
            "stop_mode":                 "or_low",
            "stop_delay_minutes":        15,
            # Trail-only exit: NO hard target close.
            # 2% trail is wide enough for 3x ETF intrabar noise (~0.9-1.5%)
            # while still catching genuine reversals. EOD close at 3:45 PM
            # captures the ~60% of trending days that continue into the close.
            "target_exit":               False,  # let trail + EOD handle exit
            "target_scale_out":          1.0,    # unused when target_exit=False
            "trail_stop_pct":            0.02,   # 2% trailing stop
            "swing_mode":                False,
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
    sentiment_base       = os.getenv("SENTIMENT_API_URL", "http://localhost:8000")
    sentiment_configured = bool(os.getenv("SENTIMENT_ADMIN_TOKEN", ""))

    print("\n" + "=" * 65)
    if swing_mode:
        print("  🌿  TREND-FILTERED ORB — SWING MODE  v17")
    else:
        print("  🚀  TREND-FILTERED ORB — ORB INTRADAY MODE  v17")
    print("=" * 65)
    print(f"  Mode              : {mode}")
    print(f"  Account type      : CASH (PDT rule does not apply)")
    if swing_mode:
        print(f"  Strategy          : Swing — multi-symbol, long holds")
        print(f"  Symbols           : symbols.txt ({_count_symbols()} symbols)")
        print(f"  Max Positions     : {PARAMS['max_positions']} concurrent")
        print(f"  Max Pos Size      : {int(PARAMS['max_position_pct']*100)}% = "
              f"~$600 per position on $2k account")
        print(f"  New entries/day   : 3 max (highest conviction from signals)")
        print(f"  Target exit       : enabled (closes at target)")
        print(f"  Trail stop        : {int(PARAMS['trail_stop_pct']*100)}%")
        print(f"  Sell cooldown     : {PARAMS['swing_sell_cooldown_days']}d")
        print(f"  Min conviction    : {PARAMS['swing_min_conviction']}")
    else:
        print(f"  Strategy          : ORB intraday — QQQ only")
        print(f"  Symbols file      : symbols.txt (should contain: QQQ)")
        print(f"  Execution         : TQQQ (bull) / SQQQ (bear)")
        print(f"  Max Positions     : {PARAMS['max_positions']} (one trade at a time)")
        print(f"  Max Pos Size      : {int(PARAMS['max_position_pct']*100)}% = "
              f"~$800 (~24 TQQQ shares at $33)")
        print(f"  Target exit       : DISABLED — trail + EOD handles all exits")
        print(f"  Trail stop        : {int(PARAMS['trail_stop_pct']*100)}% "
              f"(ratchets up, never down)")
        print(f"  1 trade/day       : re-entry blocked after STOP or target passed")
    print(f"  Base Risk/Trade   : {int(PARAMS['risk_pct']*100)}% = "
          f"~$40 at risk on $2k account")
    print(f"  Reward:Risk ref   : {PARAMS['reward_ratio']:.0f}:1 (~$80 target reference)")
    print(f"  Stop Mode         : {PARAMS['stop_mode']} "
          f"(delay {PARAMS['stop_delay_minutes']} min)")
    print(f"  ORB Iteration     : {PARAMS['sleeptime_orb']} (9:45 AM–noon)")
    print(f"  Off-ORB Iteration : {PARAMS['sleeptime_default']}")
    print(f"  After-Close Delay : {PARAMS['after_close_delay_minutes']} min")
    print(f"  AI Min Confidence : {PARAMS['ai_min_confidence']}")
    print(f"  Ollama Model      : qwen3:8b (localhost:11434)")
    print(f"  Trade Journal     : cache/trade_journal.db")
    print(f"  Log File          : {_log_file}")
    print(f"  Sentiment Alpha   : {sentiment_base}/api/v1/analyze "
          f"({'token set ✅' if sentiment_configured else 'no token ⚠️'})")
    print("=" * 65)
    print()
    if not swing_mode:
        print("  Exit logic (trail-only, ORB mode):")
        print("  • First 15 min: stop is INACTIVE (stop_delay_minutes=15)")
        print("  • After 15 min: stop arms at OR low level")
        print("  • As price rises: trail ratchets up 2% below highest seen")
        print("  • Trail hit     → close with locked-in gains")
        print("  • Target passed → log milestone, position CONTINUES")
        print("  • 3:45 PM EOD   → TQQQ/SQQQ forced close")
    print()
    print("  Daily schedule:")
    print("  • Script start    — Ollama warmup + bias refresh")
    print("  • ~9:00 AM ET     — Earnings cache cleared, regime pre-warmed")
    print("  • 9:30 AM ET      — Position sync from Alpaca")
    print("  • 9:45 AM – noon  — ORB entry window (2-min iterations)")
    print("  • Noon – 3:45 PM  — Monitor + trailing stop (5-min iterations)")
    print("  • 3:45 PM ET      — Leveraged/inverse ETFs forced close")
    print("  • 3:50 PM ET      — PRELIM EOD signals")
    print("  • ~4:05 PM ET     — FINAL EOD signals (after_market_closes)")
    print()
    print("  ⚠️  CASH ACCOUNT: funds settle T+1 after each trade close.")
    print("      1 trade/day means funds are always available next morning.")
    print()
    print("  To stop: Ctrl+C")
    print("=" * 65 + "\n")

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