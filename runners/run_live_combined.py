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

run_live_combined.py — Launch the Trend-Filtered ORB Strategy v10
──────────────────────────────────────────────────────────────────
v10: Dual-account support.

Two TrendFilteredORB instances run simultaneously on a single LumiBot Trader:

  ORB account  (ALPACA_API_KEY_ORB / ALPACA_API_SECRET_ORB)
    swing_mode = False
    Trades leveraged ETFs intraday — all positions closed by 3:45 PM.
    Direct-trade positions held overnight until SELL signal.

  Swing account (ALPACA_API_KEY_SWING / ALPACA_API_SECRET_SWING)
    swing_mode = True
    Enters on ORB breakouts, holds direct-trade positions long-term.
    90-day sell cooldown per symbol, force-sell on extreme STRONG_SELL.

Each instance has:
  - Its own Alpaca broker connection (separate API keys)
  - Its own isolated _positions / _orb_state / _trade_ids dicts
  - Its own log file  (logs/bot_orb_YYYYMMDD.log / logs/bot_swing_YYYYMMDD.log)
  - Its own bias cache (cache/daily_bias_orb.json / cache/daily_bias_swing.json)
  - Its own trade journal (cache/trade_journal_orb.db / cache/trade_journal_swing.db)

Shared across both instances (no duplication):
  - Ollama (single warmup, both instances use same ai_engine module)
  - Sentiment-Trading-Alpha (single background thread, result cached in memory)
  - Earnings cache (shared module-level dict in earnings_filter.py)

Both instances call startup_refresh() before the Trader starts, so both
accounts have fresh bias signals and earnings cache before market open.

run_live_combined.py — Launch the Trend-Filtered ORB Strategy v10
──────────────────────────────────────────────────────────────────
Dual-account mode: run two separate processes, one per account.

    python runners/run_live_combined.py --account orb
    python runners/run_live_combined.py --account swing

Each process:
  - Reads its own API keys (ALPACA_API_KEY_ORB / ALPACA_API_KEY_SWING)
  - Writes to its own bias cache and trade journal
  - Has its own log file

The start_bot.bat launcher starts both processes in separate windows.
LumiBot does not support multiple live strategies in one process.
"""

import os
import sys
import logging
import argparse
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# ── Parse account argument ────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--account", choices=["orb", "swing"], default="orb",
                    help="Which account to run: orb (day trade) or swing (overnight)")
args = parser.parse_args()
ACCOUNT = args.account

# ── Per-account config ────────────────────────────────────────────────────────
ACCOUNT_META = {
    "orb": {
        "label":          "ORB (Day Trade)",
        "key_env":        "ALPACA_API_KEY_ORB",
        "secret_env":     "ALPACA_API_SECRET_ORB",
        "paper_env":      "ALPACA_IS_PAPER_ORB",
        "bias_cache":     "cache/daily_bias_orb.json",
        "journal_db":     "cache/trade_journal_orb.db",
        "log_prefix":     "bot_orb",
        "swing_mode":     False,
    },
    "swing": {
        "label":          "SWING (Overnight)",
        "key_env":        "ALPACA_API_KEY_SWING",
        "secret_env":     "ALPACA_API_SECRET_SWING",
        "paper_env":      "ALPACA_IS_PAPER_SWING",
        "bias_cache":     "cache/daily_bias_swing.json",
        "journal_db":     "cache/trade_journal_swing.db",
        "log_prefix":     "bot_swing",
        "swing_mode":     True,
    },
}
meta = ACCOUNT_META[ACCOUNT]

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
_ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
_log_file = f"logs/{meta['log_prefix']}_{_ts}.log"

_file_handler = logging.FileHandler(_log_file, encoding="utf-8")
_file_handler.setFormatter(
    logging.Formatter(f"%(asctime)s | {ACCOUNT.upper()} | %(levelname)s | %(message)s")
)
logging.getLogger().addHandler(_file_handler)
logging.getLogger().setLevel(logging.INFO)
print(f"  [{ACCOUNT.upper()}] Logging to: {_log_file}")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lumibot.brokers import Alpaca
from lumibot.traders import Trader
from strategies.trend_filtered_orb import TrendFilteredORB


def main():
    api_key    = os.getenv(meta["key_env"])
    api_secret = os.getenv(meta["secret_env"])
    is_paper   = os.getenv(meta["paper_env"], "true").lower() == "true"

    if not api_key or not api_secret:
        print(f"❌ [{ACCOUNT.upper()}] Missing {meta['key_env']} or {meta['secret_env']} in .env")
        sys.exit(1)

    PARAMS = {
        # ── Per-instance paths ─────────────────────────────────────────────
        "bias_cache_path":   meta["bias_cache"],
        "journal_db_path":   meta["journal_db"],
        "log_file_path":     _log_file,

        # ── Iteration timing ──────────────────────────────────────────────
        "sleeptime_orb":             "2M",
        "sleeptime_default":         "5M",
        "after_close_delay_minutes": 5,

        # ── Strategy ──────────────────────────────────────────────────────
        "orb_minutes":               15,
        "bar_minutes":               5,
        "risk_pct":                  0.01,
        "reward_ratio":              2.0,
        "eod_exit_time":             "15:45",
        "max_positions":             8,
        "ai_min_confidence":         0.55,
        "hold_override":             False,
        "hold_override_size":        0.5,
        "min_stop_pct":              0.005,
        "max_position_pct":          0.15,
        "min_breakout_pct":          0.001,

        # ── Swing mode ────────────────────────────────────────────────────
        "swing_mode":                       meta["swing_mode"],
        "swing_min_conviction":             75,
        "swing_sell_cooldown_days":         90,
        "swing_force_sell_conviction":      85,
        "swing_force_sell_bear_score":      5,
    }

    broker   = Alpaca({
        "API_KEY":    api_key,
        "API_SECRET": api_secret,
        "PAPER":      is_paper,
    })
    strategy = TrendFilteredORB(
        broker=broker,
        parameters=PARAMS,
        name=ACCOUNT.upper(),
    )

    trader = Trader()
    trader.add_strategy(strategy)

    # ── Banner ─────────────────────────────────────────────────────────────
    mode                 = "📄 PAPER TRADING" if is_paper else "💰 LIVE TRADING ⚠️"
    sentiment_base       = os.getenv("SENTIMENT_API_URL", "http://localhost:8000")
    sentiment_configured = bool(os.getenv("SENTIMENT_ADMIN_TOKEN", ""))

    print("\n" + "=" * 70)
    print(f"  🚀  {meta['label'].upper()} — v10")
    print("=" * 70)
    print(f"  Account           : {ACCOUNT.upper()}")
    print(f"  Mode              : {mode}")
    print(f"  API Key           : ...{api_key[-6:]}")
    print(f"  Swing Mode        : {'✅ ON' if PARAMS['swing_mode'] else '❌ off'}")
    print(f"  Symbols           : symbols.txt ({_count_symbols()} symbols)")
    print(f"  Base Risk/Trade   : {PARAMS['risk_pct']*100:.0f}% (AI scales to 2x max)")
    print(f"  Max Positions     : {PARAMS['max_positions']}")
    print(f"  Max Position Size : {PARAMS['max_position_pct']*100:.0f}% of portfolio")
    print(f"  Min Stop Distance : {PARAMS['min_stop_pct']*100:.1f}% of price")
    print(f"  Min Breakout      : {PARAMS['min_breakout_pct']*100:.1f}% beyond OR")
    print(f"  AI Min Confidence : {PARAMS['ai_min_confidence']}")
    print(f"  ORB Iteration     : {PARAMS['sleeptime_orb']} (9:45 AM–noon)")
    print(f"  Off-ORB Iteration : {PARAMS['sleeptime_default']}")
    print(f"  After-Close Delay : {PARAMS['after_close_delay_minutes']} min")
    if PARAMS["swing_mode"]:
        print(f"  Swing Min Conv.   : {PARAMS['swing_min_conviction']}")
        print(f"  Sell Cooldown     : {PARAMS['swing_sell_cooldown_days']}d")
        print(f"  Force-Sell Conv.  : {PARAMS['swing_force_sell_conviction']}")
        print(f"  Force-Sell Bear≥  : {PARAMS['swing_force_sell_bear_score']}")
    print(f"  Ollama Model      : llama3.2:3b (localhost:11434)")
    print(f"  Bias Cache        : {meta['bias_cache']}")
    print(f"  Trade Journal     : {meta['journal_db']}")
    print(f"  Log File          : {_log_file}")
    print(f"  Sentiment Alpha   : {sentiment_base}/api/v1/analyze "
          f"({'token set ✅' if sentiment_configured else 'no token ⚠️'})")
    print("=" * 70)
    print()
    print("  Trade model: ALWAYS BUY — never short-sell")
    if PARAMS["swing_mode"]:
        print("  • BUY signal  → BUY direct-trade stock (no leveraged ETFs in swing mode)")
        print("  • SELL signal → skip new entries; close existing position if signal flips")
        print("  • HOLD signal → BUY direct-trade stock on upside breakout (0.5× size)")
    else:
        print("  • BUY signal  → BUY bull leveraged ETF (e.g. QQQ→TQQQ)")
        print("  • SELL signal → BUY inverse ETF (e.g. IBIT→BITI) if one exists")
        print("  • HOLD signal → BUY bull ETF on upside breakout (0.5× size)")
    print()
    print("  To stop: Ctrl+C")
    print("=" * 70 + "\n")

    print(f"  🔄 [{ACCOUNT.upper()}] Running startup refresh...")
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