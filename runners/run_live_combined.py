"""
run_live_combined.py — Trend-Filtered ORB Strategy
────────────────────────────────────────────────────
3-symbol ORB intraday bot for a $2,000 Alpaca CASH account.

  Signal symbols : QQQ, SMH
  QQQ  -> TQQQ (bull) / SQQQ (bear)   Nasdaq-100 3x
  SMH  -> SOXL (bull) / SOXS (bear)   Semiconductor 3x

  1 trade per symbol per day — re-entry blocked after STOP
  Trail stop (2%) + EOD close at 3:50 PM handle all exits
  Stop arms 15 min after entry (protects against early wicks)
  Stop at OR low (textbook ORB placement)

ACCOUNT NOTES:
  Use Alpaca CASH account — not margin.
  PDT rule (25k minimum on margin) does not apply to cash accounts.
  T+1 settlement: all leveraged ETFs close EOD so funds settle overnight.

Switch paper/live: set ALPACA_IS_PAPER=true/false in .env
"""

import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

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
        # IMPORTANT: prevents margin usage and PDT flags on accounts < $25k
        # Alpaca paper accounts default to margin — this forces cash-only behaviour
        "CASH_ONLY":  True,
    }

    PARAMS = {
        # ── Iteration speed ───────────────────────────────────────────────
        "sleeptime_orb":             "2M",   # always 2M — set at startup in initialize()
        "after_close_delay_minutes": 5,

        # ── Core ORB ──────────────────────────────────────────────────────
        "orb_minutes":        15,
        "bar_minutes":        5,
        "risk_pct":           0.10,    # 10% max loss per trade = $200 on $2k
        "reward_ratio":       2.0,     # 2:1 reference (~$80 target)
        "eod_exit_time":      "15:50",   # 3:50 PM - close at market hours

        # ── Position limits ($2k cash account) ───────────────────────────
        # Up to 2 concurrent positions — 1 per signal symbol (QQQ, SMH).
        # Capital split proportional to conviction when multiple signals fire simultaneously.
        "max_positions":      2,
        "max_position_pct":   1.0,    # full account deployable across positions

        # ── Size guards ───────────────────────────────────────────────────
        "min_stop_pct":       0.005,   # floor, scaled ×3 = 1.5% for TQQQ/SQQQ
        "min_breakout_pct":   0.001,   # must clear OR high by 0.1%

        # ── AI / signal ───────────────────────────────────────────────────
        "ai_min_confidence":  0.55,
        "hold_override":      False,
        "hold_override_size": 0.5,

        # ── Stop placement ────────────────────────────────────────────────
        # or_low: stop at Opening Range low — textbook ORB placement.
        # stop_delay: ignore stop for first 15 min to avoid stop-hunt wicks.
        "stop_mode":           "or_low",
        "stop_delay_minutes":  15,

        # ── Trail-only exit ───────────────────────────────────────────────
        # No hard target close. 2% trail sits above 3x ETF intrabar noise
        # (~0.9–1.5%) while catching genuine reversals.
        # EOD close at 3:45 PM handles trending days that run into close.
        "target_exit":         False,
        "target_scale_out":    1.0,    # unused
        "trail_stop_pct":      0.02,   # 2% trailing stop
        "em_boundary_exit":    True,   # close if price hits options EM upper boundary

        # ── VIX filter (#4) ───────────────────────────────────────────────────
        # Skip ALL entries if VIX >= 40 (true crisis: COVID/2008 level only)
        # VIX 30-40 = scary but ORB + inverse ETFs can profit — don't skip
        # Reduce size to 75% if VIX >= 30 (peak fear, modest risk reduction)
        # Historical: VIX only exceeded 30 once in past year (April 7, 2026)
        "vix_skip_above":      40,
        "vix_half_size_above": 30,
        "vix_size_factor":     0.75,   # 75% size when VIX >= vix_half_size_above

        # ── HOLD-bias volume gate (#6) ─────────────────────────────────────────
        # HOLD-bias entries already use 0.5x size — risk is already managed.
        # Requiring 0.8x volume (slightly below avg) keeps most trades active
        # while still filtering out the truly dead-volume entries.
        # Raise to 1.2+ for stricter filtering (risks missing morning trades).
        "hold_min_vol_ratio":  0.7,
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
    print("  🚀  TREND-FILTERED ORB — QQQ + SMH INTRADAY BOT")
    print("=" * 65)
    print(f"  Mode              : {mode}")
    print(f"  Account type      : CASH (PDT rule does not apply)")
    print(f"  Signal symbols    : QQQ, SMH  (2-symbol, best backtest)")
    print(f"  QQQ -> TQQQ/SQQQ  : Nasdaq-100 3x")
    print(f"  SMH -> SOXL/SOXS  : Semiconductor 3x")
    acct_val = 2000  # starting capital
    print(f"  Max Positions     : {PARAMS['max_positions']} (1 per signal symbol)")
    print(f"  Max Pos Size      : {int(PARAMS['max_position_pct']*100)}% of account (conviction-weighted split)")
    print(f"  Base Risk/Trade   : {int(PARAMS['risk_pct']*100)}% stop distance (~${acct_val*PARAMS['risk_pct']:,.0f} on ${acct_val:,} account)")
    print(f"  Reward:Risk ref   : {PARAMS['reward_ratio']:.0f}:1 (reference only — trail + EOD handles exits)")
    print(f"  Stop Mode         : {PARAMS['stop_mode']} (delay {PARAMS['stop_delay_minutes']} min)")
    print(f"  Target exit       : DISABLED — trail + EOD handles all exits")
    print(f"  Trail stop        : {int(PARAMS['trail_stop_pct']*100)}% (ratchets up, never down)")
    print(f"  EM boundary exit  : {PARAMS['em_boundary_exit']} — close if price hits daily expected move")
    print(f"  ORB window        : 9:45 AM – 10:45 AM (entries only in this window)")
    print(f"  Iteration         : 2M all day (instant return when idle — no performance cost)")
    print(f"  AI Min Confidence : {PARAMS['ai_min_confidence']} (Ollama qwen3:8b)")
    print(f"  Trade Journal     : cache/trade_journal.db")
    print(f"  Log File          : {_log_file}")
    print(f"  Sentiment Alpha   : {sentiment_base}/api/v1/analyze "
          f"({'token set ✅' if sentiment_configured else 'no token ⚠️'})")
    print("=" * 65)
    print()
    print("  Trade model (per symbol, 1 trade/day each):")
    print("  • QQQ BUY  -> TQQQ | QQQ SELL -> SQQQ | QQQ HOLD -> TQQQ (0.5x)")
    print("  • SMH BUY  -> SOXL | SMH SELL -> SOXS | SMH HOLD -> SOXL (0.5x)")
    print("  • Re-entry blocked after STOP — 1 trade per symbol per session")
    print()
    print("  Exit logic:")
    print(f"  • First {PARAMS['stop_delay_minutes']} min after entry: stop INACTIVE (avoids stop-hunt wicks)")
    print("  • After delay: stop arms at OR low")
    print("  • As price rises: 2% trail ratchets up, never down")
    print("  • EM boundary hit → immediate close (options expected move ceiling)")
    print("  • Trail hit        → close with locked-in gains")
    print(f"  • {PARAMS['eod_exit_time']} PM EOD   → all leveraged ETFs force-closed")
    print()
    print("  Daily schedule:")
    print("  • Script start     — Ollama warmup + bias refresh from cache")
    print("  • ~9:00 AM ET      — Earnings cache cleared, regime pre-warmed")
    print("  • 9:30 AM ET       — Position sync from Alpaca")
    print("  • 9:45–10:45 AM ET — ORB entry window (2-min iterations)")
    print("  • 10:45 AM ET      — Window closes; sleep if no position taken")
    print("  • If position open — 2-min monitoring (stop/trail/EM) until 3:50 PM")
    print(f"  • {PARAMS['eod_exit_time']} PM ET      — Force-close all leveraged ETFs")
    print("  • ~4:05 PM ET      — FINAL EOD signals written to daily_bias.json")
    print()
    print("  ⚠️  CASH ACCOUNT: all leveraged ETFs close by 3:50 PM daily.")
    print("      Funds settle T+1 — available again next morning.")
    print()
    print(f"  VIX skip >= {PARAMS['vix_skip_above']}   : skip ALL entries (extreme fear)")
    print(f"  VIX half-size >= {PARAMS['vix_half_size_above']} : halve position size (elevated vol)")
    print(f"  HOLD min vol  : {PARAMS['hold_min_vol_ratio']}x avg volume required for neutral-bias entries")
    print("  To stop: Ctrl+C")
    print("=" * 65 + "\n")

    print("  🔄 Running startup refresh (bias + earnings cache)...")
    print()
    strategy.startup_refresh()

    trader.run_all()


if __name__ == "__main__":
    main()