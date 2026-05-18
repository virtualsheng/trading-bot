"""
TrendFilteredORB Strategy - Full Architecture

Signal flow:
  EOD Technical Signal -> Daily Bias Cache
  
  Morning ORB Breakout (only if aligns with bias)
  
  AI Setup Grader (Ollama) -> Confidence Score + Size Multiplier
  
  Regime Detector (Ollama) -> Market Regime + Stop/Target Adjustment
  
  Dynamic Position Sizing (risk_pct x size_multiplier)
  
  Alpaca Execution
  
  Trade Journal (SQLite) -> ML training data

HOLD bias override: if bias=HOLD but a strong ORB signal fires
and no position exists, the trade is taken at half size.

TrendFilteredORB Strategy - v3

Key behaviors:
  - Leveraged/inverse ETFs (2x/3x): always closed at EOD
  - Direct-trade symbols (no leverage pair): held overnight,
    closed on subsequent SELL signal from signal_engine
  - On each market open: syncs open positions from Alpaca
  - ORB entry: only fires ONCE per symbol per day (at OR breakout)
  - Stop/target: monitored every 5-min iteration during market hours
  - HOLD override: disabled by default (set hold_override=True to enable)
  - Opposing position guard: won't hold bull+bear of same pair simultaneously
  - Direct-trade SHORT guard: skips SHORT if no inverse ETF exists

TrendFilteredORB Strategy - v4

Changes in v4:
  + Earnings calendar filter (skip entries within 48h of earnings)
  + Regime-based strategy switching:
      - trending_up / trending_down -> ORB momentum entries (normal)
      - ranging / mean_reversion    -> mean-reversion fade entries
      - volatile                    -> ORB only with tighter sizing
      - low_liquidity               -> skip entirely
  + Ollama warmup in before_market_opens()
  + run_technical_signals double-call bug fixed (single pass, cached results)
  + hold_override explicitly passed from launcher

Key behaviors unchanged from v3:
  - Leveraged/inverse ETFs closed at EOD
  - Direct-trade symbols held overnight, closed on SELL signal
  - Broker position sync at market open
  - ORB fires once per symbol between 9:45-noon
  - Stop/target monitored every 5-min
  - AI setup grading + dynamic sizing
  - Trade journal (SQLite)

TrendFilteredORB Strategy - v5

Changes in v5:
  + SELL signal at 3:50 PM immediately closes overnight positions
    (previously signals were updated but sells only checked next morning)
  + Second true EOD analysis at 4:15 PM using official closing prices
    overwrites the 3:50 PM preliminary bias cache with final close data
    and immediately acts on any new SELL signals from the final run
  + Market hours guard - iteration exits immediately outside
    Mon-Fri 9:30 AM - 4:25 PM ET, eliminating off-hours noise
  + Ollama warmup moved to initialize() so model loads at script start
    not at market open

TrendFilteredORB Strategy - v6

Changes in v6:
  + Swing mode removed - moved to standalone swing_signal_engine/
  + Bot now QQQ-only: signal on QQQ, execute on TQQQ (bull) or SQQQ (bear)
  + SIGNAL_SYMBOL = "QQQ" hardcoded - always trades QQQ -> TQQQ/SQQQ
  + start_bot.bat runs single instance only

Key behaviors from v3/v4/v5 unchanged:
  - TQQQ/SQQQ always closed at EOD (3:45 PM)
  - Direct-trade symbols held overnight, closed on SELL signal
  - Broker position sync at 9:30 AM market open
  - ORB entries once per symbol 9:45 AM - noon
  - Stop/target monitored every 5-min during market hours
  - Earnings filter (48h buffer before report)
  - Regime-based strategy switching (ORB vs mean-reversion)
  - AI setup grading + dynamic sizing
  - Trade journal (SQLite)

TrendFilteredORB Strategy - v7

Changes in v7 (bug fixes from live trading session 2026-05-14):

  BREAKOUT FILTER (critical fix):
    ALL entries (BUY and HOLD) now require price to meaningfully clear the
    OR boundary: current > or_high x (1 + min_breakout_pct) for LONG entries.
    Symbols showing "WAIT / Inside Range" in the ORB alert are now skipped.

  POSITION SIZING GUARDS (critical fix):
    1. min_stop_pct (default 0.5%): floor on risk_dist - prevents absurdly
       large share counts when the OR is very tight.
    2. max_position_pct (default 15%): hard cap on position value regardless
       of qty calculation.

  direction VARIABLE BUG FIXED:
    direction/exec_ticker only assigned inside the confirmed breakout block,
    eliminating the "cannot access local variable 'direction'" errors for
    UFO, GDE and other direct-trade symbols with HOLD bias.

  REGIME PROMPT REDUCED (v7 fix):
    _refresh_regime() now fetches 10/10/5 bars (5m/15m/1H) instead of
    20/20/10. fmt_bars() in ai_engine.py trims to [-5:], so the prompt
    sent to Ollama contains at most 15 OHLCV rows instead of 30.
    This halves generation time on llama3.2:3b, eliminating the 30s timeout.

  DUPLICATE LOG FIX:
    run_live_combined.py now only adds a FileHandler to the root logger.
    LumiBot's own StreamHandler handles console output - adding a second
    one caused every line to print twice.

Key behaviors from v3-v6 unchanged:
  - Leveraged/inverse ETFs closed at EOD
  - Direct-trade symbols held overnight, closed on SELL signal
  - Broker position sync at 9:30 AM market open
  - ORB entries once per symbol 9:45 AM - noon
  - Stop/target monitored every 5-min during market hours
  - Earnings filter (48h buffer before report)
  - Regime-based strategy switching (ORB vs mean-reversion)
  - AI setup grading + dynamic sizing
  - Trade journal (SQLite)
  - Swing mode (v6)

TrendFilteredORB Strategy - v8

All fixes combined:

  v7 fixes:
    - Breakout filter: require price > or_high x (1 + min_breakout_pct) before entry
    - Sizing guard 1: min_stop_pct (0.5%) - prevents huge qty on tight ORs
    - Sizing guard 2: max_position_pct (15%) - hard cap per position
    - direction variable bug fixed (UFO/GDE errors eliminated)
    - Regime prompt reduced: fetch 10/10/5 bars, fmt_bars uses [-5:]
    - Duplicate log fixed: run_live_combined adds FileHandler only

  v8 fixes:
    - FINAL signals moved to after_market_closes() - LumiBot blocks
      on_trading_iteration() after ~4:03 PM so the 4:15 PM block never fired
    - Earnings filter: "No earnings dates found" logged at DEBUG not ERROR
      (ETFs don't have earnings - this was noisy, not an error)
    - trigger_sentiment_async() now receives bias dict so STA only
      processes BUY/STRONG_BUY symbols + SPY/QQQ instead of all 40

TrendFilteredORB Strategy - v9

CRITICAL FIX in v9: Order direction and trade model completely corrected.

  All orders now submit as "buy" - the strategy never short-sells.
  Trade model:
    BUY/STRONG_BUY signal  -> BUY bull leveraged ETF (e.g. QQQ->TQQQ)
    SELL/STRONG_SELL signal -> BUY inverse ETF       (e.g. QQQ->SQQQ)
    SELL signal + no inverse ETF -> skip entirely    (e.g. RKLB, URA)
    HOLD signal -> BUY bull ETF only on upside breakout, at 0.5x size

  BUGS FIXED in v9 vs v8:
    1. HOLD bias was opening SHORT trades via is_short_break path -> removed
    2. direction="SHORT" submitted "sell" orders -> ALL orders now submit "buy"
    3. Stop/target were inverted for inverse ETF trades -> fixed, always same formula
    4. Direct-trade symbols (no inverse) were being short-sold -> blocked
    5. Candidates were ranked before direction was confirmed -> ranking now only
       includes valid confirmed setups
    6. Immediate TARGET/STOP hits after bad orders -> fixed by correct price math

  All v7/v8 fixes retained:
    - Breakout filter (min_breakout_pct)
    - Sizing guards (min_stop_pct, max_position_pct)
    - direction/exec_ticker assigned after breakout confirmed
    - Regime prompt reduced (10/10/5 bars)
    - after_market_closes() for FINAL signals
    - Earnings filter at DEBUG level

  v9 changes:
    - Dynamic sleeptime: 2M during ORB window (9:45 AM-noon), 5M otherwise
    - after_market_closes() waits after_close_delay_minutes (default 5)
      before running FINAL signals - gives closing prices time to settle
    - CRITICAL: All order direction bugs fixed:
        * Always submit BUY orders - never short-sell
        * SELL signal -> BUY inverse ETF (is_inverse=True)
        * SELL signal + no inverse ETF -> skip
        * HOLD signal -> upside breakout only, never bearish
        * Stop/target always: stop below entry, target above entry

TrendFilteredORB Strategy - v17

NEW: Trail-only exit - no hard target close. Unified $2k ORB account support.

  TARGET EXIT REMOVED (target_exit=False by default):
    The initial target is still calculated and logged as a reference milestone,
    but reaching it no longer triggers a position close. Instead:
      * The trailing stop (trail_stop_pct=0.02) does all the work
      * EOD close at 3:45 PM is the primary forced exit for leveraged ETFs
    This captures the full move on the ~60% of trending days where price
    continues into the close, instead of being capped at the first target.

  TRAILING STOP SET TO 2.0% (default):
    Previous default was 1.5% - too tight for 3x ETF intrabar noise.
    Normal TQQQ/SQQQ bar-to-bar fluctuation is 0.9-1.5%, so a 1.5% trail
    would frequently get knocked out by noise rather than genuine reversal.
    2.0% sits just above the noise floor while still catching real reversals.

  TARGET_EXIT=True: position closes fully at target price (conservative mode).
    Set in PARAMS if you prefer hard target exits over trail + EOD.

  New parameters:
    "target_exit":    False   # True = close at target; False = let trail/EOD handle
    "trail_stop_pct": 0.02    # 2% trail (was 1.5%)

  Milestone log when target is passed (target_exit=False):
    "TARGET PASSED TQQQ @ 34.56 | unrealised PnL: +$54.24 | trail=2.0% - letting it ride"

All v16 fixes retained:
  - Stop arm-into-loss detection
  - max_positions=10 live default, max_position_pct=10%

All v15 fixes retained:
  - OR-low stop placement + 15-min stop activation delay

All v14-v10 fixes retained (scale-out legacy, leverage scaling,
  re-entry block, exec price space, backtest sleep guard)

TrendFilteredORB Strategy - v16

FIX: Stop arm-into-loss detection + separated live vs backtest defaults.

  STOP ARM-INTO-LOSS FIX:
    Previously when the stop delay elapsed, the stop armed but didn't
    check whether price had ALREADY fallen below the stop level during
    the delay window. This caused the position to be held open for one
    more 2-min bar before the next monitor call caught it.
    Fix: at the moment the stop arms, immediately check current price
    against the stop level and close if already breached.

  PARAMETER SEPARATION:
    Strategy class defaults are now live-oriented:
      max_positions   = 10   (live: up to 10 concurrent)
      max_position_pct = 0.10 (live: 10% max per position)
    The backtest runner (run_backtest_combined.py) overrides these with
    conservative values suited for a 3-ticker validation run:
      max_positions   = 3    (only QQQ/TQQQ/SQQQ in backtest)
      max_position_pct = 0.05 (5% cap - avoids over-sizing on tight ORs)

All v15 fixes retained:
  - OR-low stop placement (stop_mode="or_low")
  - Stop activation delay (stop_delay_minutes=15)

All v14 fixes retained:
  - Scale-out at target + trailing stop on remainder

All v13-v10 fixes retained (leverage scaling, re-entry block,
  exec price space, backtest sleep guard)

TrendFilteredORB Strategy - v15

NEW: OR-low stop placement + stop activation delay.

  STOP PLACEMENT - stop_mode parameter (default: "or_low"):
    "or_low"    - Stop anchored at the Opening Range low, converted to
                  exec_ticker price space via the same % relationship used
                  for entry. This is the textbook ORB stop: a valid breakout
                  above OR high should NEVER revisit OR low - if it does,
                  the breakout thesis is invalidated and you exit.
                  On TQQQ/SQQQ this gives a much wider, more meaningful stop
                  than the old or_mid approach (e.g. OR = 490-494 on QQQ ->
                  stop anchored at 490 instead of 492, ~0.8% vs 0.4%).
    "or_mid"    - Legacy v14: stop at OR midpoint (tighter, more stop-outs).
    "fixed_pct" - Simple min_stop_pct below exec entry price.

  STOP ACTIVATION DELAY - stop_delay_minutes parameter (default: 15):
    The stop is NOT checked for the first 15 minutes after entry.
    This lets the trade breathe through the common early-session volatility
    and stop-hunt wicks that would otherwise trigger a stop on an otherwise
    valid breakout. After 15 minutes the stop arms normally.
    Setting stop_delay_minutes=0 restores immediate stop activation (v14).

  Together these two changes mean:
    - The stop is wider (or_low vs or_mid)
    - AND it doesn't activate until the trade has had time to develop
    - Worst case: price reverses hard in the first 15 min -> stopped at or_low
    - Best case: price dips early then rallies -> stop never triggered, full
      profit available on both halves of the scale-out

  New parameters:
    "stop_mode":          "or_low"   # "or_low" | "or_mid" | "fixed_pct"
    "stop_delay_minutes": 15         # 0 = immediate (v14 behaviour)

  Position dict gains two new keys set at entry:
    "entry_time"  - datetime of entry (for stop delay calculation)
    "stop_active" - False during delay window, True once armed

All v14 fixes retained:
  - Scale-out at target (target_scale_out=0.5)
  - Trailing stop on remainder (trail_stop_pct=0.015)

All v13 fixes retained:
  - min_stop_pct scaled by leverage multiple (1x/2x/3x)

All v12 fixes retained:
  - Re-entry blocked after STOP/TARGET (one trade per symbol per session)

All v11 fixes retained:
  - exec_ticker price space for stop/target

All v10 fixes retained:
  - Backtest sleep guard (is_backtesting)

TrendFilteredORB Strategy - v14

NEW: Scale-out at target + trailing stop on remainder.

  BEHAVIOUR:
    When price hits the initial target, instead of closing the full position
    the strategy now sells only `target_scale_out` (default 50%) of shares,
    locking in profit on half the position. The remaining 50% continues to
    run with a trailing stop, allowing winners to compound intraday.

    Trailing stop anchors at the target price after scale-out so the
    remainder can never turn into a loser - the worst case is exiting
    the second half at the target level (same as the old full-exit).

  NEW PARAMETERS:
    target_scale_out  (default 0.5)  - fraction sold at target; 1.0 = old behaviour
    trail_stop_pct    (default 0.015) - trailing stop % on remainder; 0.0 = disabled

  POSITION LIFECYCLE after scale-out:
    1. Price hits target   -> sell 50%, set pos["scaled_out"]=True, raise stop to target
    2. Price keeps rising  -> trailing stop ratchets up every 5-min bar
    3. Price reverses      -> trailing stop fires, close remaining 50%
    4. 3:45 PM EOD         -> _close_leveraged_positions closes any remainder

  _close_single_position updated to use broker live qty as source of truth,
  falling back to pos["qty_remaining"] after a partial scale-out.

All v13 fixes retained:
  - min_stop_pct scaled by leverage multiple (1x/2x/3x)

All v12 fixes retained:
  - Re-entry blocked after STOP/TARGET (one trade per symbol per session)

All v11 fixes retained:
  - exec_ticker price space for stop/target

All v10 fixes retained:
  - Backtest sleep guard (is_backtesting)

TrendFilteredORB Strategy - v13

FIX: Minimum stop distance scaled by leverage multiple.

  ROOT CAUSE: min_stop_pct=0.5% was applied flat to all exec_tickers.
  On a 3x leveraged ETF (TQQQ, SQQQ, SOXL etc) 0.5% is equivalent to only
  ~0.17% on the underlying - smaller than normal intrabar bid/ask spread noise.
  Result: stop-outs within seconds of entry even after the v11 price-space fix.

  FIX: min_stop_pct is now multiplied by the leverage factor of exec_ticker:
    1x ETFs / direct trades  -> min_stop_pct x 1.0  (unchanged, e.g. 0.5%)
    2x ETFs                  -> min_stop_pct x 2.0  (e.g. 1.0%)
    3x ETFs                  -> min_stop_pct x 3.0  (e.g. 1.5%)

  With default min_stop_pct=0.5%, a 3x ETF now has a 1.5% stop floor,
  which corresponds to ~0.5% on the underlying - the intended behaviour.
  The leverage multiple is detected from exec_ticker name at runtime.

All v12 fixes retained:
  - Re-entry blocked after STOP/TARGET (one trade per symbol per session)

All v11 fixes retained:
  - exec_ticker price space for stop/target

All v10 fixes retained:
  - Backtest sleep guard (is_backtesting)

TrendFilteredORB Strategy - v12

FIX: Block re-entry after STOP or TARGET exits (one trade per symbol per session).

  ROOT CAUSE: _close_single_position() always reset trade_taken=False on any
  close, allowing the strategy to immediately re-enter the same symbol on the
  very next 2-min ORB iteration. On volatile sessions this created 6+ round
  trips per symbol per day, which is not the intended ORB behaviour.

  FIX: STOP and TARGET exits now keep trade_taken=True for the rest of the
  trading day. EOD, SELL_SIGNAL, PRELIM/FINAL_SELL_SIGNAL, and STRATEGY_END
  closes still reset to False - those are intentional position changes, not
  failed breakouts.

  Expected effect: trade count drops from ~2/day to ~1/day per symbol.
  The daily reset in on_trading_iteration clears _orb_state each morning so
  the block never carries over to the next session.

All v11 fixes retained:
  - exec_ticker price space for stop/target (v11)
  - Backtest sleep guard (v10)
  - All v9 order direction fixes
  - All v7/v8 sizing guards and breakout filter

TrendFilteredORB Strategy - v11

CRITICAL FIX in v11: Stop/target calculated in exec_ticker price space.

  ROOT CAUSE: stop and target were derived from the signal symbol's price
  (e.g. QQQ @ $490) but compared against the exec_ticker's price
  (e.g. SQQQ @ $170). This caused an immediate stop-out on every single
  bar since SQQQ's price never comes near a $490-derived stop level.

  FIX: Convert the OR risk distance to a % of the signal symbol's price,
  then apply that same % to exec_ticker's actual live price:
    risk_pct_of_signal = abs(current - or_mid) * stop_adj / current
    exec_risk_dist     = exec_current * risk_pct_of_signal
    initial_stop       = exec_current - exec_risk_dist
    initial_target     = exec_current + exec_risk_dist * reward_ratio

  This ensures stop/target are always valid price levels for the actual
  security being traded, regardless of the signal/exec price ratio.

  Also:
  - entry_price in journal and _positions now uses exec_current (correct fill)
  - Log message now shows both signal price and exec price:
    "QQQ(490.23)->SQQQ x30 @ 171.45 | Stop:170.59 Target:173.03"
  - exec_current fetched before sizing; returns None if unavailable

All v10 fixes retained:
  - Backtest sleep guard (is_backtesting)
  - All v9 order direction fixes
  - All v7/v8 sizing guards and breakout filter
"""

import os
import json
import logging
import time
import pandas as pd
from datetime import datetime, time as dtime, date as ddate
from lumibot.strategies import Strategy

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.signal_engine import get_technical_signal
from strategies.leverage_map import get_leveraged_pair, is_direct_trade, get_all_signal_symbols, get_all_exec_tickers
from strategies.ai_engine import (
    check_ollama_available, grade_setup,
    detect_regime, get_cached_regime, narrate_trade,
)
from strategies.trade_journal import TradeJournal
try:
    from strategies.expected_move import get_expected_move, get_qqq_expected_move, get_all_expected_moves, em_context_for_trade
    _EM_AVAILABLE = True
except ImportError:
    _EM_AVAILABLE = False
from notifications.emailer import send_email
from notifications.discord import send_discord_message
from notifications.telegram import send_telegram_message
try:
    from strategies.premarket_signals import (
        enrich_bias, trigger_sentiment_async, premarket_conviction_boost
    )
    _PREMARKET_AVAILABLE = True
except ImportError:
    _PREMARKET_AVAILABLE = False

logger = logging.getLogger(__name__)

# Signal symbols the bot monitors. Execution ETFs come from leverage_map.py.
# QQQ -> TQQQ (bull) / SQQQ (bear)   Nasdaq-100 3x
# SMH -> SOXL (bull) / SOXS (bear)   Semiconductor 3x
# USO  → UCO (bull) / SCO (bear)   Oil 2x
SIGNAL_SYMBOLS = ["QQQ", "SMH", "USO"]
SIGNAL_SYMBOL  = SIGNAL_SYMBOLS[0]   # legacy compat - primary signal for logging
BIAS_CACHE          = "cache/daily_bias.json"
BIAS_CACHE_BACKTEST = "cache/daily_bias_backtest.json"

SIGNAL_PRELIM_HOUR   = 15
SIGNAL_PRELIM_MINUTE = 50

MARKET_OPEN_TIME  = dtime(9, 30)
MARKET_CLOSE_TIME = dtime(16, 25)

# ORB entry window - 2-min iterations during this window
ORB_ENTRY_START = dtime(9, 45)
ORB_ENTRY_END   = dtime(10, 30)  # 10:30 AM cutoff - late ORB entries have lower win rates

# All execution tickers - derived from leverage_map so adding a new symbol
# to LEVERAGE_MAP automatically includes its ETFs here.
LEVERAGED_TICKERS = get_all_exec_tickers()   # {"TQQQ","SQQQ","SOXL","SOXS","UCO","SCO"}


def is_leveraged(ticker: str) -> bool:
    return ticker.upper() in LEVERAGED_TICKERS


class TrendFilteredORB(Strategy):

    parameters = {
        "sleeptime_orb":             "2M",
        "sleeptime_default":         "5M",
        "after_close_delay_minutes": 5,
        "orb_minutes":               15,
        "bar_minutes":               5,
        "risk_pct":                  0.10,   # max loss per trade as % of portfolio (10% = $200 on $2k)
        "reward_ratio":              2.0,
        "eod_exit_time":             "15:50",   # 3:50 PM - close before PRELIM signals, guarantees market hours
        # Live defaults - backtest runner overrides these in PARAMS
        "max_positions":             3,    # 3 trade at a time (QQQ, SMH, USO)
        "ai_min_confidence":         0.55,
        "hold_override":             False,
        "hold_override_size":        0.5,
        "min_stop_pct":              0.005,
        # max_position_pct = total capital to deploy across all active positions.
        # For a single symbol (QQQ only): 1.0 = use full account on one trade.
        # For multiple symbols: capital is split proportional to conviction score.
        #   e.g. QQQ cv=83, SMH cv=52 -> QQQ gets 61%, SMH gets 39% of total pool.
        # risk_pct remains the max loss per trade regardless of position size.
        "max_position_pct":          1.0,   # total capital to deploy across all positions (1.0 = full account)
        "min_breakout_pct":          0.001,
        #  Trailing stop 
        # trail_stop_pct: trailing stop % below the highest price seen since entry.
        #   The stop ratchets up as price rises but never moves down.
        #   2.0% is the recommended default for 3x leveraged ETFs (TQQQ/SQQQ):
        #     - Normal intrabar noise on TQQQ is ~0.9-1.5% (3x QQQ's 0.3-0.5%)
        #     - 2% is just above that noise floor - won't get knocked out by a
        #       single noisy bar, but will catch a genuine trend reversal
        #     - On the strongest trending days EOD close beats any trailing stop
        #       (~60% of trending days continue into the close) so the EOD forced
        #       exit at 3:45 PM is the primary exit; trail catches the other 40%
        #   0.0 = disabled (hold to EOD or until SELL signal)
        "trail_stop_pct":      0.02,

        #  Target behaviour 
        # target_exit: whether to close the position when the initial target is hit.
        #   False (recommended for ORB): let the trail and EOD handle the exit.
        #     The initial_target is still calculated and logged for reference,
        #     but reaching it does NOT trigger a close. The trade rides as long
        #     as the trail isn't hit, capturing larger moves on strong trend days.
        # target_exit=False: trail stop + EOD close handle all exits.
        # target_exit=True: close at target (conservative setups).
        "target_exit":         False,
        "target_scale_out":    1.0,    # unused when target_exit=False

        #  Stop placement & delay 
        # stop_mode:
        #   "or_low"    - stop at OR low (textbook ORB placement)
        #   "or_mid"    - legacy stop at OR midpoint (tighter)
        #   "fixed_pct" - fixed % below exec entry price
        # stop_delay_minutes:
        #   Minutes after entry before the stop becomes active.
        #   Protects against stop-hunt wicks in the first 15 min.
        "stop_mode":           "or_low",
        "stop_delay_minutes":  15,
    }

    #  Lifecycle 

    def initialize(self):
        # Start with default sleeptime - switches to ORB speed at 9:45 AM
        self.sleeptime = self.parameters.get("sleeptime_default", "5M")
        self.set_market("NYSE")

        self._starting_capital     = self.portfolio_value
        self._last_date            = None
        self._prelim_signals_done  = False
        self._final_signals_done   = False
        self._market_opened_today  = False
        self._regime_checked_at    = None
        self._in_orb_window        = False

        self._orb_state   = {}
        self._positions   = {}
        self._trade_ids   = {}
        self._traded_today = set()  # symbols traded today - blocks re-entry
        self._daily_bias = self._load_bias()
        self._journal    = TradeJournal()

        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            self.log_message("BACKTEST MODE - AI/regime skipped for speed")
        else:
            try:
                available = check_ollama_available()
                if available:
                    self.log_message("Ollama ready - AI grading active")
                else:
                    self.log_message(
                        "!  Ollama unavailable - trades will use fallback "
                        "confidence (0.5x size). Run: ollama serve"
                    )
            except Exception as e:
                self.log_message(f"Ollama warmup error: {e}")

        self.log_message(
            f"Initialized | signal: {SIGNAL_SYMBOL} -> TQQQ/SQQQ | "
            f"portfolio: ${self.portfolio_value:,.2f} | "
            f"sleeptime: ORB={self.parameters['sleeptime_orb']} "
            f"default={self.parameters['sleeptime_default']}"
        )

    def startup_refresh(self):
        """Called from run_live_combined.py before trader.run_all()."""
        print("[startup] Refreshing bias signals...")
        try:
            self._run_eod_signals(label="STARTUP")
        except Exception as e:
            print(f"[startup] Bias refresh failed: {e} - using cached bias")

        print("[startup] Pre-warming earnings cache...")
        try:
            from strategies.earnings_filter import prefetch_earnings
            ETF_SYMBOLS = {"QQQ", "SMH", "SPY", "USO", "DIA", "XLK", "XLF",
                           "TQQQ", "SQQQ", "SOXL", "SOXS", "UCO", "SCO"}
            # Only prefetch for non-ETF symbols - ETFs have no earnings
            non_etf = [s for s in self._load_symbols() if s.upper() not in ETF_SYMBOLS]
            if non_etf:
                prefetch_earnings(non_etf)
                print(f"[startup] Earnings cache ready for {len(non_etf)} symbols")
            else:
                print("[startup] All symbols are ETFs - skipping earnings prefetch")
        except Exception as e:
            print(f"[startup] Earnings pre-fetch skipped: {e}")

        print(f"[startup] Ready | bias: {len(self._daily_bias)} symbols\n")

        if _PREMARKET_AVAILABLE:
            try:
                trigger_sentiment_async(
                    self._load_symbols(),
                    bias=self._daily_bias,
                )
            except Exception as e:
                print(f"[startup] Sentiment trigger failed: {e}")

    def before_market_opens(self):
        try:
            from strategies.earnings_filter import clear_cache
            clear_cache()
        except Exception:
            pass

        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() != "true":
            self._refresh_regime("QQQ")
            self._regime_checked_at = self.get_datetime()

        bias_date = next(iter(self._daily_bias.values()), {}).get("date") if self._daily_bias else None
        today_str = self.get_datetime().date().strftime("%Y-%m-%d")
        if not self._daily_bias or bias_date != today_str:
            self.log_message(f"Bias is from {bias_date or 'empty'} - refreshing for {today_str}")
            self._run_eod_signals(label="PRE-MARKET")
        else:
            self.log_message(
                f"Bias current ({bias_date}, {len(self._daily_bias)} symbols) - "
                f"skipping pre-market refresh"
            )

        if _PREMARKET_AVAILABLE and not os.getenv("LUMIBOT_BACKTEST_MODE", ""):
            try:
                api_key    = os.getenv("ALPACA_API_KEY", "")
                api_secret = os.getenv("ALPACA_API_SECRET", "")
                self._daily_bias = enrich_bias(
                    self._daily_bias, api_key, api_secret, run_sentiment=True
                )
                self._save_bias(self._daily_bias)
                gap_ups   = sum(1 for v in self._daily_bias.values() if v.get("gap_signal") == "GAP_UP")
                gap_downs = sum(1 for v in self._daily_bias.values() if v.get("gap_signal") == "GAP_DOWN")
                self.log_message(
                    f"Pre-market enrichment complete | gaps: {gap_ups} up / {gap_downs} down"
                )
            except Exception as e:
                self.log_message(f"Pre-market enrichment failed: {e} - using technical bias only")

    def after_market_closes(self):
        """
        Safety net EOD close + FINAL signals.
        Force-closes any leveraged ETF still open (should have been caught
        at 3:50 PM intraday but backtest timing can miss it).
        Then runs FINAL EOD signals on official closing prices.
        """
        # Safety net: no leveraged ETF should ever be held overnight
        leveraged_open = [k for k in self._positions if is_leveraged(k)]
        if leveraged_open:
            self.log_message(
                f"WARNING after_market_closes: {leveraged_open} still open "
                f"- force closing now"
            )
            self._close_leveraged_positions("AFTER_CLOSE_SAFETY")

        if self._final_signals_done:
            return

        delay = self.parameters.get("after_close_delay_minutes", 5)
        # v10 FIX: skip real sleep during backtests
        if delay > 0 and not self.is_backtesting:
            self.log_message(
                f"After-close - waiting {delay} min for closing prices to settle..."
            )
            time.sleep(delay * 60)

        self.log_message("After-close - running FINAL EOD signals (official close prices)")
        try:
            self._run_eod_signals(label="FINAL")
            self._final_signals_done = True
        except Exception as e:
            self.log_message(f"FINAL signals error: {e}")

    #  Main iteration 

    def on_trading_iteration(self):
        now   = self.get_datetime()
        today = now.date()

        if today != self._last_date:
            self._last_date           = today
            self._prelim_signals_done = False
            self._final_signals_done  = False
            self._market_opened_today = False
            self._in_orb_window       = False
            self._orb_state           = {}
            self._traded_today        = set()   # reset daily trade tracker

        is_weekday = now.weekday() < 5
        in_session = MARKET_OPEN_TIME <= now.time() <= MARKET_CLOSE_TIME
        if not is_weekday or not in_session:
            return

        if not self._market_opened_today:
            self._sync_positions_from_broker()
            self._market_opened_today = True
            self.log_message(f"Market open | {len(self._positions)} positions carried")

        #  2-minute iterations all day 
        # ORB entry window: 9:45-10:30 AM
        # Position monitoring (stop/trail/EM): active until position closes
        # Once ORB window closes and no positions held, only EOD signals matter
        in_orb = ORB_ENTRY_START <= now.time() <= ORB_ENTRY_END
        if self.sleeptime != "2M":
            self.sleeptime = "2M"
        if in_orb and not self._in_orb_window:
            self._in_orb_window = True
            self.log_message("ORB window open (9:45-10:30 AM)")
        elif not in_orb and self._in_orb_window:
            self._in_orb_window = False
            if not self._positions:
                self.log_message("ORB window closed - no position taken today, monitoring EOD signals only")
            else:
                self.log_message("ORB window closed - monitoring open position every 2M")

        # Skip iterations after ORB window if no positions - nothing to do
        # until EOD signal runs at 3:50 PM.
        # IMPORTANT: never skip if we have open positions - must reach EOD close.
        if not in_orb and not self._positions:
            if now.time() < dtime(SIGNAL_PRELIM_HOUR, SIGNAL_PRELIM_MINUTE):
                return  # nothing to do - wait for EOD signals

        eod_h, eod_m = map(int, self.parameters["eod_exit_time"].split(":"))
        at_eod = now.time() >= dtime(eod_h, eod_m)

        if at_eod:
            # 1. Close all leveraged positions first — still market hours at 3:50
            self._close_leveraged_positions("EOD")
            # 2. Then run PRELIM signals on just-closed prices
            if not self._prelim_signals_done:
                self.log_message("3:50 PM - running preliminary EOD signals")
                self._run_eod_signals(label="PRELIM")
                self._prelim_signals_done = True
            return  # nothing else to do after EOD close

        if (now.time() >= dtime(SIGNAL_PRELIM_HOUR, SIGNAL_PRELIM_MINUTE)
                and not self._prelim_signals_done):
            self.log_message("3:50 PM - running preliminary EOD signals")
            self._run_eod_signals(label="PRELIM")
            self._prelim_signals_done = True

        if now.time() >= dtime(eod_h, eod_m):
            return


        if (os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() != "true" and
                (self._regime_checked_at is None or
                 (now - self._regime_checked_at).seconds >= 1800)):
            self._refresh_regime("QQQ")
            self._regime_checked_at = now

        self._monitor_open_positions()

        #  ORB entries 9:45 AM - 10:30 AM 
        if in_orb:
            max_pos    = self.parameters["max_positions"]
            slots_free = max_pos - len(self._positions)

            if slots_free > 0:
                candidates = []
                for symbol in self._load_symbols():
                    if symbol in self._traded_today:
                        continue
                    try:
                        c = self._process_symbol(symbol, now, today)
                        if c is not None:
                            candidates.append(c)
                    except Exception as e:
                        self.log_message(f"Error {symbol}: {e}")

                if candidates:
                    candidates.sort(key=lambda x: x["conviction"], reverse=True)
                    ranked_log = ", ".join(
                        f"{c['symbol']}({c['conviction']:.0f})" for c in candidates
                    )
                    self.log_message(
                        f"Candidates [{len(candidates)}] ranked: {ranked_log} | "
                        f"slots free: {slots_free}/{max_pos}"
                    )
                    # Conviction-weighted capital allocation across candidates
                    top_candidates = candidates[:slots_free]
                    capital_alloc  = self._allocate_capital(top_candidates)
                    executed = 0
                    for c in top_candidates:
                        if executed >= slots_free or len(self._positions) >= max_pos:
                            break
                        c["allocated_capital"] = capital_alloc.get(
                            c["symbol"],
                            self.portfolio_value * self.parameters.get("max_position_pct", 1.0)
                        )
                        self._execute_candidate(c)
                        executed += 1

    #  Broker Position Sync 

    def _sync_positions_from_broker(self):
        try:
            alpaca_positions = self.get_positions()
            synced = []
            for pos in alpaca_positions:
                ticker = pos.asset.symbol
                qty    = int(pos.quantity) if pos.quantity else 0
                if qty == 0 or ticker in self._positions:
                    continue
                avg_price = float(pos.avg_fill_price) if pos.avg_fill_price else 0.0
                overnight = not is_leveraged(ticker)

                stop   = avg_price * 0.95
                target = avg_price * 1.10
                try:
                    row = self._journal.get_open_trade(ticker)
                    if row:
                        stop   = row.get("initial_stop",   stop)
                        target = row.get("initial_target", target)
                        self.log_message(
                            f"Restored {ticker}: stop={stop:.2f} target={target:.2f} (from journal)"
                        )
                    else:
                        self.log_message(
                            f"No journal record for {ticker} - using default "
                            f"stop={stop:.2f} target={target:.2f}"
                        )
                except Exception:
                    pass

                self._positions[ticker] = {
                    "symbol": ticker, "signal_symbol": ticker,
                    "exec_ticker": ticker,
                    "is_inverse": False,
                    "direction": "LONG",
                    "entry_price": avg_price, "stop": stop, "target": target,
                    "qty": qty, "entry_value": self.portfolio_value,
                    "overnight_ok": overnight, "synced": True,
                }
                synced.append(ticker)
                if is_leveraged(ticker):
                    self.log_message(f"!  {ticker} is leveraged and open - will close at EOD")
            if synced:
                self.log_message(f"Synced from Alpaca: {synced}")
        except Exception as e:
            self.log_message(f"Position sync failed: {e}")

    #  Sell Signal Exit 

    def _check_and_close_sell_signals(self, reason: str = "SELL_SIGNAL"):
        """
        Close positions when the underlying signal reverses.

        Bull ETF / direct position: close on SELL/STRONG_SELL signal.
        Inverse ETF position: close on BUY/STRONG_BUY signal (underlying reversed).
        """
        for exec_ticker, pos in list(self._positions.items()):
            signal_symbol = pos.get("signal_symbol", exec_ticker)
            bias          = self._daily_bias.get(signal_symbol, {})
            bias_signal   = bias.get("signal", "HOLD")
            is_inverse    = pos.get("is_inverse", False)

            # Bull ETF: close on bearish signal
            # Inverse ETF: close on bullish signal (underlying reversed)
            if is_inverse:
                should_close  = bias_signal in ("BUY", "STRONG_BUY")
                close_reason  = f"underlying reversed to {bias_signal}"
            else:
                should_close  = bias_signal in ("SELL", "STRONG_SELL")
                close_reason  = f"signal reversed to {bias_signal}"

            if not should_close:
                continue

            self.log_message(
                f"Signal exit [{reason}] | closing {exec_ticker} - {close_reason}"
            )

            try:
                bars = self.get_historical_prices(exec_ticker, 2, "5m")
                exit_price = (float(bars.df["close"].iloc[-1])
                              if bars and len(bars.df) > 0
                              else pos["entry_price"])
            except Exception:
                exit_price = pos["entry_price"]

            self._close_single_position(exec_ticker, pos, reason, exit_price)

    #  Stop/Target Monitor 

    def _monitor_open_positions(self):
        """
        All positions are LONG (we always buy).

        Exit logic (v17 - trail-only, no hard target exit):
          1. Activate stop after stop_delay_minutes. During the delay window
             the stop is completely ignored - protects against early wicks.
          2. Once active: ratchet trailing stop up as price rises.
             The stop only ever moves UP, never down.
          3. If stop hit -> close position.
          4. Target is NOT an exit trigger (target_exit=False by default).
             The initial_target is logged for reference but reaching it does
             NOT close the trade. The trail and EOD are the only exits.
             Set target_exit=True for hard target exit (conservative).

        Why trail-only beats scale-out for ORB:
          On the ~60% of trending days where price continues into the close,
          a hard target exit cuts the trade short. The 2% trail is wide enough
          to survive intrabar noise (TQQQ noise ~ 0.9-1.5%) while catching
          genuine reversals. EOD close at 3:45 PM handles the rest.

        pos dict keys managed here:
          "stop"        - current stop level (ratcheted up by trail)
          "stop_active" - False during delay window, True once armed
          "target"      - reference level only (not an exit trigger)
          "entry_time"  - datetime of entry (for stop delay calc)
          "qty"         - original quantity
        """
        trail_pct    = self.parameters.get("trail_stop_pct",    0.02)
        target_exit  = self.parameters.get("target_exit",       False)
        scale_out    = self.parameters.get("target_scale_out",  1.0)
        stop_delay   = self.parameters.get("stop_delay_minutes", 15)
        now          = self.get_datetime()

        for exec_ticker, pos in list(self._positions.items()):
            try:
                bars = self.get_historical_prices(exec_ticker, 2, "5m")
                if bars is None or len(bars.df) == 0:
                    continue
                current = float(bars.df["close"].iloc[-1])

                #  1. Stop delay: arm after N minutes 
                if not pos.get("stop_active", False):
                    entry_time = pos.get("entry_time")
                    if entry_time is not None:
                        elapsed = (now - entry_time).total_seconds() / 60.0
                        if elapsed >= stop_delay:
                            pos["stop_active"] = True
                            self.log_message(
                                f"Stop armed for {exec_ticker} after {elapsed:.0f} min "
                                f"| stop={pos['stop']:.2f}"
                            )
                            # v16 FIX: close immediately if already below stop
                            if current <= pos["stop"]:
                                self.log_message(
                                    f"Stop triggered at arm time for {exec_ticker} "
                                    f"- price {current:.2f} already below "
                                    f"stop {pos['stop']:.2f}"
                                )
                                self._close_single_position(
                                    exec_ticker, pos, "STOP", current
                                )
                                continue
                    else:
                        pos["stop_active"] = True  # fallback: arm immediately

                #  2. Trailing stop ratchet 
                # Only ratchet once the stop is active and price is profitable.
                if pos.get("stop_active", False) and trail_pct > 0:
                    if current > pos["entry_price"]:
                        new_trail = current * (1.0 - trail_pct)
                        if new_trail > pos["stop"]:
                            pos["stop"] = new_trail  # ratchet up, never down

                #  3. Stop hit -> close 
                if pos.get("stop_active", False) and current <= pos["stop"]:
                    self._close_single_position(exec_ticker, pos, "STOP", current)
                    continue

                #  4. EM upper boundary exit 
                # If price reaches the options-implied daily expected move upper
                # boundary, the market has priced in its maximum expected move.
                # Skipped in backtest: today's EM options prices don't apply to
                # historical TQQQ/SQQQ prices from prior years.
                em_exit    = self.parameters.get("em_boundary_exit", True)
                is_backtest = os.getenv("LUMIBOT_BACKTEST_MODE","").lower() == "true"
                if em_exit and _EM_AVAILABLE and not is_backtest and not pos.get("em_exit_checked", False):
                    try:
                        sig_sym = pos.get("signal_symbol", "QQQ")
                        em = get_expected_move(sig_sym)
                        if em:
                            # Use exec_ticker upper bound
                            em_upper = em.get("exec_daily_upper", 0)
                            if em_upper > 0 and current >= em_upper:
                                pnl_now = (current - pos["entry_price"]) * pos.get("qty", 0)
                                self.log_message(
                                    f"EM BOUNDARY HIT {exec_ticker} @ ${current:.2f} "
                                    f">= upper EM ${em_upper:.2f} "
                                    f"| PnL: ${pnl_now:+.2f} - closing before EOD"
                                )
                                self._close_single_position(
                                    exec_ticker, pos, "EM_TARGET", current)
                                continue
                    except Exception:
                        pass

                #  5. Hard target exit or milestone log 
                target = pos.get("target", 0)
                if target_exit and current >= target and not pos.get("target_logged", False):
                    # target_exit=True: close immediately (conservative)
                    self._close_single_position(exec_ticker, pos, "TARGET", current)
                    continue
                elif not target_exit and current >= target and not pos.get("target_logged", False):
                    # target_exit=False (default): log milestone, keep riding
                    pos["target_logged"] = True
                    pnl_now = (current - pos["entry_price"]) * pos.get("qty", 0)
                    self.log_message(
                        f"TARGET PASSED {exec_ticker} @ {current:.2f} "
                        f"| unrealised PnL: ${pnl_now:+.2f} "
                        f"| trail={trail_pct*100:.1f}% - letting it ride"
                    )

            except Exception as e:
                self.log_message(f"Monitor error {exec_ticker}: {e}")

    def _close_single_position(self, exec_ticker: str, pos: dict,
                                reason: str, exit_price: float):
        # After a scale-out, only qty_remaining shares are still open.
        # Always ask the broker for the live position quantity as the
        # source of truth - don't rely solely on our internal counter.
        try:
            position    = self.get_position(exec_ticker)
            broker_qty  = int(position.quantity) if position else 0
            # Use broker qty if available; fall back to our tracked remainder
            close_qty   = broker_qty if broker_qty > 0 else pos.get("qty_remaining", pos.get("qty", 0))
            if close_qty > 0:
                self.submit_order(
                    self.create_order(exec_ticker, close_qty, "sell")
                )
            else:
                # Nothing left to close (already fully scaled out)
                self._positions.pop(exec_ticker, None)
                self._trade_ids.pop(exec_ticker, None)
                return
        except Exception as e:
            self.log_message(f"Close order failed {exec_ticker}: {e}")
            return

        pnl      = self.portfolio_value - pos["entry_value"]
        trade_id = self._trade_ids.get(exec_ticker)

        if trade_id:
            try:
                narrative = narrate_trade({
                    **pos, "exit_reason": reason,
                    "pnl": round(pnl, 2), "exit_price": exit_price,
                })
                self._journal.close_trade(
                    trade_id=trade_id, exit_price=exit_price,
                    exit_reason=reason, portfolio_value=self.portfolio_value,
                    ai_narrative=narrative,
                )
            except Exception:
                pass

        o_tag   = "overnight" if pos.get("overnight_ok") else "intraday"
        inv_tag = " [inverse]" if pos.get("is_inverse") else ""
        msg     = (
            f"CLOSED {exec_ticker}{inv_tag} ({reason}) @ {exit_price:.2f} | "
            f"PnL: ${pnl:+.2f} | {o_tag}"
        )
        self.log_message(msg)
        self._notify(f"Trade-Bot: EXIT {exec_ticker}", msg)

        self._positions.pop(exec_ticker, None)
        self._trade_ids.pop(exec_ticker, None)
        signal_symbol = pos.get("signal_symbol", pos.get("symbol"))
        if signal_symbol and signal_symbol in self._orb_state:
            # v12 FIX: block re-entry after STOP or TARGET exits.
            # Previously trade_taken was always reset to False on close, allowing
            # the strategy to immediately re-enter on the very next 2-min bar.
            # On volatile days this created 6+ round trips per symbol per day.
            # Only STOP and TARGET exits block re-entry for the rest of the day.
            # EOD, SELL_SIGNAL, PRELIM/FINAL_SELL_SIGNAL, STRATEGY_END all still
            # reset to False (the next-day reset in on_trading_iteration handles
            # tomorrow's clean slate regardless).
            if reason in {"STOP", "TARGET"}:
                self.log_message(
                    f"Re-entry blocked for {signal_symbol} today ({reason}) - "
                    f"1 trade per symbol per session"
                )
                # trade_taken stays True - no re-entry until next trading day
            else:
                self._orb_state[signal_symbol]["trade_taken"] = False

    #  EOD: Close Leveraged/Inverse 

    def _close_leveraged_positions(self, reason: str):
        to_close = {
            k: v for k, v in self._positions.items()
            if is_leveraged(k) or not v.get("overnight_ok", True)
        }
        if not to_close:
            overnight = [k for k in self._positions if not is_leveraged(k)]
            if overnight:
                self.log_message(f"EOD: keeping overnight: {overnight}")
            return
        for exec_ticker, pos in list(to_close.items()):
            try:
                bars = self.get_historical_prices(exec_ticker, 2, "5m")
                exit_price = (float(bars.df["close"].iloc[-1])
                              if bars and len(bars.df) > 0
                              else pos["entry_price"])
            except Exception:
                exit_price = pos["entry_price"]
            self._close_single_position(exec_ticker, pos, reason, exit_price)
        overnight = list(self._positions.keys())
        if overnight:
            self.log_message(f"EOD done | holding overnight: {overnight}")

    #  EOD Signal Runner 

    def _run_eod_signals(self, label: str = "EOD"):
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            self._run_eod_signals_backtest(label)
            return

        api_key    = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_API_SECRET")
        symbols = self._load_symbols()   # ["QQQ", "SMH"]
        self.log_message(f"[{label}] Running signals for {len(symbols)} symbols: {symbols}")
        new_bias = {}
        buys, sells = [], []

        for symbol in symbols:
            try:
                result = get_technical_signal(symbol, api_key, secret_key)
                action = result.get("action", "HOLD")
                new_bias[symbol] = {
                    "action":     action,
                    "bull_score": result.get("bull_score", 0),
                    "bear_score": result.get("bear_score", 0),
                    "rsi":        result.get("rsi", 50),
                    "vol_ratio":  result.get("volume_ratio", 1.0),
                    "date":       str(datetime.now().date()),
                    "source":     label,
                }
                if action in ("BUY", "STRONG_BUY"):
                    buys.append(symbol)
                elif action in ("SELL", "STRONG_SELL"):
                    sells.append(symbol)
            except Exception as e:
                self.log_message(f"[{label}] Signal error {symbol}: {e}")
                new_bias[symbol] = {
                    "action": "HOLD",
                    "date":   str(datetime.now().date()),
                    "source": label,
                }

        self._daily_bias = new_bias
        self._save_bias(new_bias)

        summary = (
            f"[{label}] Signals complete | "
            f"BUY:{len(buys)} SELL:{len(sells)} "
            f"HOLD:{len(symbols)-len(buys)-len(sells)}\n"
            f"Buys:  {', '.join(buys[:12])}\n"
            f"Sells: {', '.join(sells[:12])}"
        )
        # Fetch and log QQQ expected move for next session
        em_text = ""
        if _EM_AVAILABLE and label in ("FINAL", "PRELIM", "EOD"):
            try:
                all_ems = get_all_expected_moves(force=True)
                em_lines = []
                for sym, em in all_ems.items():
                    pair = get_leveraged_pair(sym)
                    bull = pair["bull"]; bear = pair["bear"]
                    em_lines.append(
                        f"{sym}: daily ${em['daily_em']:.2f} ({em['daily_em_pct']:.1f}%) "
                        f"[${em['daily_lower']:.2f}-${em['daily_upper']:.2f}] | "
                        f"{bull}/{bear} ${em['exec_daily_em']:.2f} "
                        f"[${em['exec_daily_lower']:.2f}-${em['exec_daily_upper']:.2f}]"
                    )
                if em_lines:
                    em_text = "\n" + "\n".join(em_lines)
                    for line in em_lines:
                        self.log_message(f"[{label}] EM: {line}")
            except Exception as e:
                self.log_message(f"[{label}] EM fetch failed: {e}")

        self.log_message(summary)
        # EOD signal emails suppressed - swing_signal_engine sends a richer
        # EOD report at 4:15 PM covering QQQ and all retirement accounts.
        # Trading bot only notifies on actual trades (entry + exit).
        # To re-enable: uncomment the line below.
        # self._notify(f"Trade-Bot: [{label}] EOD Signals", summary + em_text)

    def _run_eod_signals_backtest(self, label: str):
        all_symbols   = self._load_symbols()
        avail_symbols = []
        for s in all_symbols:
            try:
                bars = self.get_historical_prices(s, 3, "5m")
                if bars is not None and len(bars.df) > 0:
                    avail_symbols.append(s)
            except Exception:
                pass

        new_bias = {}
        buys, sells = [], []

        for symbol in avail_symbols:
            try:
                bars = self.get_historical_prices(symbol, 50, "5m")
                if bars is None or len(bars.df) < 10:
                    continue
                df    = bars.df.copy()
                close = df["close"]
                ema2  = float(close.ewm(span=2,  adjust=False).mean().iloc[-1])
                ema3  = float(close.ewm(span=3,  adjust=False).mean().iloc[-1])
                ema5  = float(close.ewm(span=5,  adjust=False).mean().iloc[-1])
                rsi   = float(close.ewm(span=14, adjust=False).mean().iloc[-1])

                if ema2 > ema3 > ema5:
                    action = "BUY"
                    buys.append(symbol)
                elif ema2 < ema3 < ema5:
                    action = "SELL"
                    sells.append(symbol)
                else:
                    action = "HOLD"

                new_bias[symbol] = {
                    "action":     action,
                    "bull_score": 3 if action == "BUY"  else 0,
                    "bear_score": 3 if action == "SELL" else 0,
                    "rsi":        rsi,
                    "vol_ratio":  1.0,
                    "date":       str(self.get_datetime().date()),
                    "source":     label,
                }
            except Exception as e:
                self.log_message(f"[BT][{label}] Signal error {symbol}: {e}")

        self._daily_bias = new_bias
        self._save_bias(new_bias)
        self.log_message(
            f"[BT][{label}] Done | "
            f"BUY:{len(buys)} SELL:{len(sells)} "
            f"HOLD:{len(avail_symbols)-len(buys)-len(sells)}"
        )

    #  Regime Refresh 

    def _refresh_regime(self, symbol: str):
        try:
            bars_5m = self.get_historical_prices(symbol, 10, "5m")
            if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
                bars_15m = bars_1h = None
            else:
                bars_15m = self.get_historical_prices(symbol, 10, "15m")
                bars_1h  = self.get_historical_prices(symbol,  5, "1H")

            if bars_5m is None or len(bars_5m.df) < 5:
                return

            def to_list(bars):
                if bars is None or len(bars.df) == 0:
                    return []
                return [{"o": r["open"], "h": r["high"],
                          "l": r["low"],  "c": r["close"], "v": r["volume"]}
                        for _, r in bars.df.iterrows()]

            close  = bars_5m.df["close"]
            rsi_14 = float(close.ewm(span=14, adjust=False).mean().iloc[-1])
            atr_14 = float(
                (bars_5m.df["high"] - bars_5m.df["low"])
                .rolling(min(14, len(bars_5m.df))).mean().iloc[-1]
            )

            regime = detect_regime(
                symbol=symbol,
                bars_5m=to_list(bars_5m), bars_15m=to_list(bars_15m),
                bars_1h=to_list(bars_1h), rsi_14=rsi_14, atr_14=atr_14,
            )
            self._journal.log_regime(symbol, regime)
            self.log_message(
                f"Regime [{symbol}]: {regime.get('regime')} "
                f"({regime.get('orb_suitability')}) conf={regime.get('confidence',0):.2f}"
            )
        except Exception as e:
            self.log_message(f"Regime refresh failed: {e}")

    #  Per-Symbol ORB Entry 

    def _process_symbol(self, symbol: str, now, today):
        """
        Evaluate whether to enter a position. Always returns a BUY candidate.

        BUY/STRONG_BUY + upside breakout  -> BUY pair["bull"]  (is_inverse=False)
        SELL/STRONG_SELL + no inverse     -> SKIP (direct-trade, can't go bearish)
        SELL/STRONG_SELL + downside break -> BUY pair["bear"]  (is_inverse=True)
        HOLD + upside breakout            -> BUY pair["bull"] at 0.5x (is_inverse=False)
        HOLD + no breakout / downside     -> SKIP
        """
        bias   = self._daily_bias.get(symbol, {"action": "HOLD"})
        action = bias.get("action", "HOLD")

        hold_bias = (action == "HOLD")
        want_bull = action in ("BUY", "STRONG_BUY")
        want_bear = action in ("SELL", "STRONG_SELL")

        pair   = get_leveraged_pair(symbol)
        direct = is_direct_trade(symbol)

        # SELL signal + no inverse ETF -> skip
        if want_bear and direct:
            return None

        if not want_bull and not hold_bias and not want_bear:
            return None

        # Earnings filter
        # ETFs never have earnings - skip the check entirely
        # QQQ and SMH are ETFs; earnings filter only applies to individual stocks
        ETF_SYMBOLS = {"QQQ", "SMH", "SPY", "USO", "DIA", "XLK", "XLF",
                       "TQQQ", "SQQQ", "SOXL", "SOXS", "UCO", "SCO"}
        if symbol.upper() not in ETF_SYMBOLS:
            try:
                from strategies.earnings_filter import is_earnings_safe, get_earnings_info
                if not is_earnings_safe(symbol):
                    info = get_earnings_info(symbol)
                    self.log_message(f"SKIP {symbol} - earnings in {info.get('hours_until','?')}h")
                    return None
            except Exception:
                pass

        if symbol not in self._orb_state:
            self._orb_state[symbol] = {
                "or_high": None, "or_low": None, "or_mid": None,
                "or_established": False, "trade_taken": False,
            }
        # Block re-entry: symbol already traded today (any reason)
        if symbol in self._traded_today:
            return None
        state = self._orb_state[symbol]
        if state["trade_taken"]:
            return None

        bars = self.get_historical_prices(symbol, 20, "5m")
        if bars is None or len(bars.df) < 3:
            return None
        df = bars.df.copy()

        try:
            tz       = df.index.tz
            df_today = df[df.index.normalize() == pd.Timestamp(today, tz=tz)]
        except Exception:
            df_today = df[pd.to_datetime(df.index.date) == pd.Timestamp(today)]

        if len(df_today) < 3:
            return None

        if not state["or_established"]:
            w                = df_today.iloc[:3]
            state["or_high"] = float(w["high"].max())
            state["or_low"]  = float(w["low"].min())
            state["or_mid"]  = (state["or_high"] + state["or_low"]) / 2
            state["or_established"] = True

        current = float(df_today["close"].iloc[-1])

        #  Regime 
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            regime          = {"regime": "trending", "confidence": 0.5,
                               "orb_suitability": "moderate",
                               "stop_adjustment": 1.0, "target_adjustment": 1.0}
            regime_type     = "trending"
            regime_conf     = 0.5
            orb_suitability = "moderate"
            stop_adj        = 1.0
            target_adj      = 1.0
        else:
            regime          = get_cached_regime(symbol) or {}
            regime_type     = regime.get("regime", "unknown")
            regime_conf     = regime.get("confidence", 0.5)
            orb_suitability = regime.get("orb_suitability", "moderate")
            stop_adj        = regime.get("stop_adjustment",   1.0)
            target_adj      = regime.get("target_adjustment", 1.0)

            if regime_type == "low_liquidity":
                self.log_message(f"SKIP {symbol} - low_liquidity regime")
                return None

        #  Breakout check 
        min_breakout = self.parameters.get("min_breakout_pct", 0.001)
        is_upside    = current > state["or_high"] * (1 + min_breakout)
        is_downside  = current < state["or_low"]  * (1 - min_breakout)

        # Determine direction and exec ticker
        if want_bull and is_upside:
            is_inverse  = False
            exec_ticker = pair["bull"] if not direct else symbol
        elif want_bear and is_downside:
            is_inverse  = True
            exec_ticker = pair["bear"]
        elif hold_bias and is_upside:
            is_inverse  = False
            exec_ticker = pair["bull"] if not direct else symbol
        else:
            return None  # No valid breakout

        # Block opposing position
        if exec_ticker in self._positions:
            return None
        if is_inverse and pair.get("bull") in self._positions:
            return None
        if not is_inverse and pair.get("bear") in self._positions:
            return None

        #  AI grading 
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            grading = {"approve": True, "confidence": 0.7, "size_multiplier": 1.0}
        else:
            candles = [{"o": r["open"], "h": r["high"],
                         "l": r["low"],  "c": r["close"], "v": r["volume"]}
                       for _, r in df_today.iterrows()]
            avg_vol = float(df_today["volume"].mean()) if not df_today.empty else 1.0
            grading = grade_setup(
                symbol=symbol,
                direction="SHORT" if is_inverse else "LONG",  # AI context only
                candles=candles,
                or_high=state["or_high"], or_low=state["or_low"],
                current_price=current, avg_volume=avg_vol,
            )

        ai_min = self.parameters.get("ai_min_confidence", 0.55)
        if grading["confidence"] < ai_min or not grading.get("approve", True):
            self.log_message(
                f"SKIP {symbol} - AI {grading['confidence']:.2f} | "
                f"{grading.get('reasoning','')[:80]}"
            )
            return None

        #  Sizing 
        base_risk    = self.parameters["risk_pct"]
        size_mult    = grading.get("size_multiplier", 1.0)
        if hold_bias:
            size_mult *= self.parameters["hold_override_size"]
        if regime_type == "volatile":
            size_mult *= 0.75
        effective_risk = min(base_risk * size_mult, 0.02)

        #  v11/v13 FIX: stop/target in exec_ticker price space, scaled for leverage 
        # signal symbol (e.g. QQQ) trades at ~$490; exec_ticker (e.g. SQQQ)
        # trades at ~$170. Stop must be in exec_ticker's price space AND must
        # account for leverage multiplier - a 3x ETF needs 3x the minimum stop
        # distance to avoid being stopped out on normal intrabar noise.
        try:
            exec_bars    = self.get_historical_prices(exec_ticker, 2, "5m")
            exec_current = (float(exec_bars.df["close"].iloc[-1])
                            if exec_bars is not None and len(exec_bars.df) > 0
                            else None)
        except Exception:
            exec_current = None

        if exec_current is None or exec_current <= 0:
            self.log_message(f"SKIP {symbol} - could not fetch {exec_ticker} price for sizing")
            return None

        # Detect leverage multiple from exec_ticker name (2x or 3x)
        # Used to scale min_stop_pct so 3x ETFs get a wider stop floor.
        _et = exec_ticker.upper()
        if any(_et.startswith(p) for p in ("TQQQ","SQQQ","SPXL","SPXS","SOXL","SOXS",
                                            "UPRO","SPXU","FAS","FAZ","ERX","ERY",
                                            "JNUG","JDST","BITU","BITI",
                                            "NVDL","NVDD","TSMU","PTIR","AGQ","ZSL")):
            lev_mult = 3.0
        elif any(_et.startswith(p) for p in ("UGL","GLL","QLD","QID","SSO","SDS",
                                              "UCO","SCO","BITX")):
            lev_mult = 2.0
        else:
            lev_mult = 1.0

        #  Stop placement (v15: or_low mode is the default) 
        # Convert the chosen stop anchor to a % of the signal symbol's price,
        # then apply that % to exec_ticker's price to get the actual stop level.
        stop_mode    = self.parameters.get("stop_mode", "or_low")
        min_stop_pct = self.parameters.get("min_stop_pct", 0.005) * lev_mult

        if stop_mode == "or_low":
            # Textbook ORB stop: price should never revisit the OR low after
            # a valid breakout. Stop is placed at the OR low, converted to
            # exec_ticker price space via the same % relationship.
            or_low_pct         = state["or_low"] / max(current, 0.01)
            stop_pct_of_signal = (1.0 - or_low_pct) * stop_adj
            # Floor: ensure stop isn't trivially tight even on a wide OR
            stop_pct_of_signal = max(stop_pct_of_signal, min_stop_pct)
            exec_risk_dist     = exec_current * stop_pct_of_signal

        elif stop_mode == "fixed_pct":
            # Simple fixed % stop below exec entry
            stop_pct_of_signal = min_stop_pct
            exec_risk_dist     = exec_current * stop_pct_of_signal

        else:  # "or_mid" - legacy behaviour
            risk_pct_of_signal = abs(current - state["or_mid"]) * stop_adj / max(current, 0.01)
            risk_pct_of_signal = max(risk_pct_of_signal, min_stop_pct)
            exec_risk_dist     = exec_current * risk_pct_of_signal

        if exec_risk_dist <= 0:
            return None

        #  EM stop floor: widen stop if it's inside expected-move noise 
        # Skipped in backtest: today's options prices don't apply to historical dates.
        if _EM_AVAILABLE and os.getenv("LUMIBOT_BACKTEST_MODE","").lower() != "true":
            try:
                em = get_expected_move(symbol)   # QQQ or SMH
                if em:
                    # EM in exec_ticker space: signal EM x leverage
                    em_floor_dist = (em["daily_em"] * lev_mult) / 3.0
                    if exec_risk_dist < em_floor_dist:
                        self.log_message(
                            f"Stop widened: OR-low gave ${exec_risk_dist:.2f} "
                            f"< EM floor ${em_floor_dist:.2f} "
                            f"(QQQ daily EM ${em['daily_em']:.2f} x {lev_mult}x / 3)"
                        )
                        exec_risk_dist = em_floor_dist
            except Exception:
                pass

        # Stop below exec entry, target above - always BUY, want price UP
        initial_stop   = exec_current - exec_risk_dist
        initial_target = exec_current + exec_risk_dist * self.parameters["reward_ratio"] * target_adj

        # Sizing: conviction-weighted capital allocation.
        # allocated_capital is set by _allocate_capital() in the calling loop
        # and injected into the candidate dict before _execute_candidate runs.
        # Here we use the full max_position_pct pool as default; it will be
        # overridden by the actual conviction-weighted allocation at execution time.
        allocated_capital = self.portfolio_value * self.parameters.get("max_position_pct", 1.0)
        risk_dollars  = self.portfolio_value * effective_risk
        qty_from_val  = int(allocated_capital / max(exec_current, 0.01))
        qty_from_risk = int(risk_dollars / exec_risk_dist) if exec_risk_dist > 0 else qty_from_val
        qty           = min(qty_from_val, qty_from_risk)
        if qty < 1 and qty_from_val >= 1:
            qty = qty_from_val   # use value-based qty if risk calc gives 0

        if qty < 1:
            return None

        #  Conviction 
        score_key  = "bear_score" if is_inverse else "bull_score"
        sym_score  = bias.get(score_key, 0)
        vol_ratio  = bias.get("vol_ratio", 1.0)
        act_bonus  = 1 if action in ("STRONG_BUY", "STRONG_SELL") else 0
        conviction = (
            grading["confidence"] * 40
            + sym_score * 8
            + min(vol_ratio - 1.0, 1.0) * 10
            + act_bonus * 10
        )

        if _PREMARKET_AVAILABLE:
            try:
                pm_boost         = premarket_conviction_boost(bias)
                gap_signal       = bias.get("gap_signal", "FLAT")
                sentiment_signal = bias.get("sentiment_signal", "HOLD")
                gap_aligned  = (not is_inverse and gap_signal == "GAP_UP") or \
                               (is_inverse     and gap_signal == "GAP_DOWN")
                sent_aligned = (not is_inverse and sentiment_signal == "LONG") or \
                               (is_inverse     and sentiment_signal == "SHORT")
                if gap_aligned or sent_aligned:
                    conviction += pm_boost
                elif gap_signal != "FLAT" and not gap_aligned:
                    conviction -= pm_boost * 0.5
            except Exception:
                pass

        trade_type = "BEAR[inverse]" if is_inverse else ("HOLD-BIAS" if hold_bias else "BULL")

        return {
            "symbol": symbol, "exec_ticker": exec_ticker,
            "is_inverse": is_inverse,
            "trade_type": trade_type,
            "current": current,           # signal symbol price (QQQ etc) - for logging
            "exec_current": exec_current, # exec_ticker price (TQQQ/SQQQ etc) - for orders
            "qty": qty,
            "initial_stop": initial_stop, "initial_target": initial_target,
            "effective_risk": effective_risk, "size_mult": size_mult,
            "hold_bias": hold_bias, "conviction": conviction,
            "grading": grading, "regime": regime, "regime_type": regime_type,
            "orb_suitability": orb_suitability, "stop_adj": stop_adj,
            "target_adj": target_adj, "bias": bias, "action": action,
            "state": state, "direct": direct, "df_today": df_today,
        }

    def _allocate_capital(self, candidates: list) -> dict:
        """
        Allocate portfolio capital across candidates proportional to conviction.

        Single symbol:  full max_position_pct pool goes to that symbol.
        Multiple symbols: pool split by conviction weight.

          total_pool = min(portfolio_value, available_cash) x max_position_pct
          symbol_allocation = total_pool x (symbol_cv / sum_all_cv)

        Uses available cash (not total portfolio value) to avoid trying to deploy
        capital that is already tied up in open positions.

        Example - $2k account, max_position_pct=1.0, QQQ cv=83, SMH cv=52:
          total_pool = $2,000
          QQQ weight = 83/(83+52) = 61.5% -> $1,230
          SMH weight = 52/(83+52) = 38.5% -> $  770
        """
        # Use available cash, not total portfolio value — open positions tie up capital
        available_cash = float(self.get_cash() or 0)
        max_pct        = self.parameters.get("max_position_pct", 1.0)
        total_pool     = min(self.portfolio_value * max_pct, available_cash)
        if not candidates:
            return {}
        if len(candidates) == 1:
            return {candidates[0]["symbol"]: total_pool}

        total_cv   = sum(max(c.get("conviction", 50), 1) for c in candidates)
        allocation = {}
        for c in candidates:
            cv     = max(c.get("conviction", 50), 1)
            weight = cv / total_cv
            alloc  = round(total_pool * weight, 2)
            allocation[c["symbol"]] = alloc
            self.log_message(
                f"  Allocation: {c['symbol']} cv={cv:.0f} -> "
                f"{weight:.0%} = ${alloc:,.0f}"
            )
        return allocation

    def _execute_candidate(self, c: dict):
        symbol       = c["symbol"]
        exec_ticker  = c["exec_ticker"]
        is_inverse   = c["is_inverse"]
        trade_type   = c["trade_type"]
        signal_price = c["current"]       # signal symbol price (QQQ etc) - logging only
        exec_current = c["exec_current"]  # exec_ticker price (TQQQ/SQQQ) - used for journal/pos
        qty          = c["qty"]
        grading      = c["grading"]
        regime       = c["regime"]
        bias         = c["bias"]
        action       = c["action"]
        state        = c["state"]
        direct       = c["direct"]

        # Recalculate qty using conviction-weighted allocated_capital
        # (set by _allocate_capital before this call, may differ from _process_symbol estimate)
        allocated_capital = c.get("allocated_capital",
            self.portfolio_value * self.parameters.get("max_position_pct", 1.0))
        risk_dollars  = self.portfolio_value * c.get("effective_risk", self.parameters["risk_pct"])
        stop_dist     = abs(exec_current - c["initial_stop"])
        if stop_dist > 0 and exec_current > 0:
            qty_from_val  = int(allocated_capital / exec_current)
            qty_from_risk = int(risk_dollars / stop_dist)
            qty           = min(qty_from_val, qty_from_risk)
            if qty < 1 and qty_from_val >= 1:
                qty = qty_from_val
            if qty < 1:
                self.log_message(f"SKIP {exec_ticker} - qty=0 after allocation recalc "
                                 f"(alloc=${allocated_capital:.0f}, stop_dist=${stop_dist:.2f})")
                return

        # Always BUY - never short-sell
        try:
            order = self.create_order(exec_ticker, qty, "buy", time_in_force="day")
            self.submit_order(order)
        except Exception as e:
            self.log_message(f"Order failed {exec_ticker}: {e}")
            return

        # Inverse ETFs always close at EOD - no overnight holding
        overnight_eligible = direct and not is_inverse

        trade_id = self._journal.open_trade(
            symbol=symbol, exec_ticker=exec_ticker,
            direction="LONG" if not is_inverse else "LONG_INVERSE",
            entry_price=exec_current, quantity=qty,
            or_high=state["or_high"], or_low=state["or_low"],
            or_mid=state["or_mid"], initial_stop=c["initial_stop"],
            initial_target=c["initial_target"], risk_pct=c["effective_risk"],
            signal_action=action, signal_source="technical+ORB",
            bull_score=bias.get("bull_score", 0),
            bear_score=bias.get("bear_score", 0),
            signal_rsi=bias.get("rsi", 50),
            signal_vol_ratio=bias.get("vol_ratio", 1.0),
            ai_confidence=grading["confidence"],
            ai_size_mult=c["size_mult"],
            ai_flags=grading.get("flags", []),
            ai_vol_quality=grading.get("volume_quality", "unknown"),
            ai_pa_quality=grading.get("price_action_quality", "unknown"),
            ai_approved=grading.get("approve", True),
            regime=regime.get("regime", "unknown"),
            regime_conf=regime.get("confidence", 0.5),
            orb_suitability=c["orb_suitability"],
            stop_adjustment=c["stop_adj"], target_adjustment=c["target_adj"],
            portfolio_value=self.portfolio_value,
            open_positions=len(self._positions),
        )

        self._traded_today.add(symbol)   # block re-entry this session
        self._positions[exec_ticker] = {
            "symbol": symbol, "signal_symbol": symbol,
            "exec_ticker": exec_ticker, "is_inverse": is_inverse,
            "direction": "LONG",
            "entry_price": exec_current,   # exec_ticker price for PnL tracking
            "stop": c["initial_stop"],     # already in exec_ticker price space
            "target": c["initial_target"], # already in exec_ticker price space
            "qty": qty,
            "entry_value": self.portfolio_value, "overnight_ok": overnight_eligible,
            # v15: stop delay - time after entry before stop becomes active
            "entry_time":      self.get_datetime(),
            "stop_active":     False,  # becomes True after stop_delay_minutes
        }
        self._trade_ids[exec_ticker] = trade_id
        state["trade_taken"] = True

        o_tag     = " [OVERNIGHT]" if overnight_eligible else " [EOD]"
        inv_tag   = " -> buying inverse ETF" if is_inverse else ""

        # EM context - validate stop/target against signal symbol expected move
        em_note = ""
        if _EM_AVAILABLE and os.getenv("LUMIBOT_BACKTEST_MODE","").lower() != "true":
            try:
                em = get_expected_move(symbol)   # QQQ or SMH
                if em:
                    ctx = em_context_for_trade(
                        em, exec_current,
                        c["initial_stop"], c["initial_target"], exec_ticker
                    )
                    em_note = f" | EM:{ctx['quality']}(${ctx['em_val']:.2f})"
                    if ctx.get("beyond_em"):
                        em_note += " "
                    if ctx["quality"] == "tight":
                        self.log_message(
                            f"!  Stop inside EM noise: {ctx['notes']}")
            except Exception:
                pass

        msg = (
            f"{trade_type} | {symbol}({signal_price:.2f})->{exec_ticker} x{qty} "
            f"@ {exec_current:.2f} | Stop:{c['initial_stop']:.2f} "
            f"Target:{c['initial_target']:.2f} | "
            f"AI:{grading['confidence']:.2f}({c['size_mult']:.1f}x) | "
            f"Conviction:{c['conviction']:.0f} | "
            f"Regime:{regime.get('regime','?')}{inv_tag}{o_tag}{em_note}"
        )
        self.log_message(msg)
        self._notify(f"Trade-Bot: BUY {exec_ticker} ({trade_type})", msg)

    #  Helpers 

    def _notify(self, subject: str, body: str):
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            return
        full = f"{subject}\n{body}"
        try: send_email(subject, body)
        except Exception: pass
        try: send_discord_message(full)
        except Exception: pass
        try: send_telegram_message(full)
        except Exception: pass

    def _load_symbols(self) -> list:
        # Returns all signal symbols from the leverage map.
        # Add new symbols by updating leverage_map.py - no other change needed.
        return list(SIGNAL_SYMBOLS)

    def _bias_path(self) -> str:
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            return BIAS_CACHE_BACKTEST
        return BIAS_CACHE

    def _load_bias(self) -> dict:
        try:
            os.makedirs("cache", exist_ok=True)
            path = self._bias_path()
            if os.path.exists(path):
                with open(path) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_bias(self, bias: dict):
        try:
            os.makedirs("cache", exist_ok=True)
            with open(self._bias_path(), "w") as f:
                json.dump(bias, f, indent=2)
        except Exception as e:
            self.log_message(f"Bias save failed: {e}")

    #  Shutdown 

    def on_strategy_end(self):
        self._close_leveraged_positions("STRATEGY_END")
        try:
            stats = self._journal.get_stats(days=30)
            self.log_message(
                f"30-day: {stats.get('total_trades',0)} trades | "
                f"WR:{stats.get('win_rate',0)}% | "
                f"PnL:${stats.get('total_pnl',0):+.2f} | "
                f"PF:{stats.get('profit_factor',0):.2f}"
            )
            self._journal.export_csv()
        except Exception as e:
            self.log_message(f"Stats error: {e}")

    def on_abrupt_closing(self):
        self.sell_all()