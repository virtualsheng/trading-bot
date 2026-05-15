"""
TrendFilteredORB Strategy — Full Architecture
──────────────────────────────────────────────
Signal flow:
  EOD Technical Signal → Daily Bias Cache
  ↓
  Morning ORB Breakout (only if aligns with bias)
  ↓
  AI Setup Grader (Ollama) → Confidence Score + Size Multiplier
  ↓
  Regime Detector (Ollama) → Market Regime + Stop/Target Adjustment
  ↓
  Dynamic Position Sizing (risk_pct × size_multiplier)
  ↓
  Alpaca Execution
  ↓
  Trade Journal (SQLite) → ML training data

HOLD bias override: if bias=HOLD but a strong ORB signal fires
and no position exists, the trade is taken at half size.

TrendFilteredORB Strategy — v3
────────────────────────────────
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

TrendFilteredORB Strategy — v4
────────────────────────────────
Changes in v4:
  + Earnings calendar filter (skip entries within 48h of earnings)
  + Regime-based strategy switching:
      - trending_up / trending_down → ORB momentum entries (normal)
      - ranging / mean_reversion    → mean-reversion fade entries
      - volatile                    → ORB only with tighter sizing
      - low_liquidity               → skip entirely
  + Ollama warmup in before_market_opens()
  + run_technical_signals double-call bug fixed (single pass, cached results)
  + hold_override explicitly passed from launcher

Key behaviors unchanged from v3:
  - Leveraged/inverse ETFs closed at EOD
  - Direct-trade symbols held overnight, closed on SELL signal
  - Broker position sync at market open
  - ORB fires once per symbol between 9:45–noon
  - Stop/target monitored every 5-min
  - AI setup grading + dynamic sizing
  - Trade journal (SQLite)

TrendFilteredORB Strategy — v5
────────────────────────────────
Changes in v5:
  + SELL signal at 3:50 PM immediately closes overnight positions
    (previously signals were updated but sells only checked next morning)
  + Second true EOD analysis at 4:15 PM using official closing prices
    overwrites the 3:50 PM preliminary bias cache with final close data
    and immediately acts on any new SELL signals from the final run
  + Market hours guard — iteration exits immediately outside
    Mon–Fri 9:30 AM – 4:25 PM ET, eliminating off-hours noise
  + Ollama warmup moved to initialize() so model loads at script start
    not at market open

TrendFilteredORB Strategy — v6
────────────────────────────────
Changes in v6:
  + Swing mode (SWING_MODE=true in .env or swing_mode param):
      - No leveraged/inverse ETFs — trades underlying ETF directly
      - LONG only — no short entries
      - Entry gated on swing_min_conviction threshold
      - SELL throttled by swing_sell_cooldown_days (default 90d)
      - Force-sell override: bypasses cooldown when ALL THREE gates fire:
          conviction >= swing_force_sell_conviction (default 85)
          bear_score >= swing_force_sell_bear_score (default 5)
          action == STRONG_SELL
      - _last_swing_sell dict tracks last sell date per symbol

Key behaviors from v3/v4/v5 unchanged:
  - Leveraged/inverse ETFs closed at EOD (irrelevant in swing mode)
  - Direct-trade symbols held overnight, closed on SELL signal
  - Broker position sync at 9:30 AM market open
  - ORB entries once per symbol 9:45 AM – noon
  - Stop/target monitored every 5-min during market hours
  - Earnings filter (48h buffer before report)
  - Regime-based strategy switching (ORB vs mean-reversion)
  - AI setup grading + dynamic sizing
  - Trade journal (SQLite)

TrendFilteredORB Strategy — v7
────────────────────────────────
Changes in v7 (bug fixes from live trading session 2026-05-14):

  BREAKOUT FILTER (critical fix):
    ALL entries (BUY and HOLD) now require price to meaningfully clear the
    OR boundary: current > or_high × (1 + min_breakout_pct) for LONG entries.
    Symbols showing "WAIT / Inside Range" in the ORB alert are now skipped.

  POSITION SIZING GUARDS (critical fix):
    1. min_stop_pct (default 0.5%): floor on risk_dist — prevents absurdly
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
    LumiBot's own StreamHandler handles console output — adding a second
    one caused every line to print twice.

Key behaviors from v3–v6 unchanged:
  - Leveraged/inverse ETFs closed at EOD
  - Direct-trade symbols held overnight, closed on SELL signal
  - Broker position sync at 9:30 AM market open
  - ORB entries once per symbol 9:45 AM – noon
  - Stop/target monitored every 5-min during market hours
  - Earnings filter (48h buffer before report)
  - Regime-based strategy switching (ORB vs mean-reversion)
  - AI setup grading + dynamic sizing
  - Trade journal (SQLite)
  - Swing mode (v6)

TrendFilteredORB Strategy — v8
────────────────────────────────
All fixes combined:

  v7 fixes:
    - Breakout filter: require price > or_high × (1 + min_breakout_pct) before entry
    - Sizing guard 1: min_stop_pct (0.5%) — prevents huge qty on tight ORs
    - Sizing guard 2: max_position_pct (15%) — hard cap per position
    - direction variable bug fixed (UFO/GDE errors eliminated)
    - Regime prompt reduced: fetch 10/10/5 bars, fmt_bars uses [-5:]
    - Duplicate log fixed: run_live_combined adds FileHandler only

  v8 fixes:
    - FINAL signals moved to after_market_closes() — LumiBot blocks
      on_trading_iteration() after ~4:03 PM so the 4:15 PM block never fired
    - Earnings filter: "No earnings dates found" logged at DEBUG not ERROR
      (ETFs don't have earnings — this was noisy, not an error)

TrendFilteredORB Strategy — v8
────────────────────────────────
All fixes:

  v7: breakout filter, sizing guards, direction bug, reduced regime prompt
  v8: after_market_closes() for FINAL signals, earnings filter log level fix
  v8.1: trigger_sentiment_async() now receives bias dict so STA only
        processes BUY/STRONG_BUY symbols + SPY/QQQ instead of all 40

TrendFilteredORB Strategy — v9
────────────────────────────────
CRITICAL FIX in v9: Order direction and trade model completely corrected.

THE CORRECT TRADE MODEL (never short-sell):
  ─────────────────────────────────────────
  We ONLY ever submit BUY orders. We never short-sell.

  BUY/STRONG_BUY signal on symbol:
    → BUY the bull leveraged ETF (e.g. QQQ BUY → buy TQQQ)
    → If no leveraged ETF, BUY the symbol directly
    → Stop below entry, target above entry

  SELL/STRONG_SELL signal on symbol:
    → BUY the bear/inverse leveraged ETF (e.g. IBIT SELL → buy BITI)
    → If bull==bear (direct-trade, no inverse), SKIP — do NOT trade
    → Stop below entry (of the inverse ETF), target above entry
    → We own the inverse ETF as a LONG position

  HOLD signal on symbol:
    → ONLY enter if price breaks out ABOVE OR High (bullish breakout)
    → BUY the bull leveraged ETF or the symbol directly
    → NEVER enter bearish on a HOLD signal
    → Skip if price is inside range or breaking down

  Stop/target:
    → Always: stop = entry - risk_dist, target = entry + risk_dist × reward
    → Same formula for both bull and bear ETF entries
    → We always WANT the price to go UP after we buy

  Closing:
    → Stop hit: price falls below stop → SELL to close
    → Target hit: price rises above target → SELL to close
    → EOD: leveraged positions closed at 3:45 PM
    → SELL signal: if we hold a bull ETF and the underlying gets a SELL
      signal → close. If we hold an inverse ETF and the underlying gets
      a BUY signal → close.

BUGS FIXED in v9 vs v8:
  1. HOLD bias was opening SHORT trades via is_short_break path → removed
  2. direction="SHORT" submitted "sell" orders → ALL orders now submit "buy"
  3. Stop/target were inverted for inverse ETF trades → fixed, always same formula
  4. Direct-trade symbols (no inverse) were being short-sold → blocked
  5. Candidates were ranked before direction was confirmed → ranking now only
     includes valid confirmed setups
  6. Immediate TARGET/STOP hits after bad orders → fixed by correct price math

All v7/v8 fixes retained:
  - Breakout filter (min_breakout_pct)
  - Sizing guards (min_stop_pct, max_position_pct)
  - direction/exec_ticker assigned after breakout confirmed
  - Regime prompt reduced (10/10/5 bars)
  - after_market_closes() for FINAL signals
  - Earnings filter at DEBUG level

v9 changes:
  - Dynamic sleeptime: 2M during ORB window (9:45 AM–noon), 5M otherwise
  - after_market_closes() waits after_close_delay_minutes (default 5)
    before running FINAL signals — gives closing prices time to settle
  - CRITICAL: All order direction bugs fixed:
      * Always submit BUY orders — never short-sell
      * SELL signal → BUY inverse ETF (is_inverse=True)
      * SELL signal + no inverse ETF → skip
      * HOLD signal → upside breakout only, never bearish
      * Stop/target always: stop below entry, target above entry

All v7/v8 fixes retained:
  - Breakout filter, sizing guards, direction variable bug
  - Regime prompt reduced, after_market_closes for FINAL signals
  - Earnings filter at DEBUG level

TrendFilteredORB Strategy — v10
────────────────────────────────
v10: Dual-account support.

Each instance is fully isolated:
  - bias_cache_path  (parameter) — separate JSON bias file per account
  - journal_db_path  (parameter) — separate SQLite journal per account
  - log_file_path    (parameter) — separate log file per account
  - _positions / _orb_state / _trade_ids — per-instance dicts (already was)

startup_refresh() accepts skip_sentiment=True so the second instance
doesn't fire a duplicate STA background thread — the first instance's
thread already cached the result.

All v9 fixes retained:
  - Always BUY, never short-sell
  - Inverse ETF on SELL signal, skip if no inverse
  - HOLD = upside breakout only
  - Dynamic sleeptime (2M ORB / 5M default)
  - after_market_closes() with delay for FINAL signals
  - Earnings filter, sizing guards, breakout filter

TrendFilteredORB Strategy — v10
────────────────────────────────
v10: Dual-account support.

Each instance is fully isolated:
  - bias_cache_path  (parameter) — separate JSON bias file per account
  - journal_db_path  (parameter) — separate SQLite journal per account
  - log_file_path    (parameter) — separate log file per account
  - _positions / _orb_state / _trade_ids — per-instance dicts (already was)

startup_refresh() accepts skip_sentiment=True so the second instance
doesn't fire a duplicate STA background thread — the first instance's
thread already cached the result.

All v9 fixes retained:
  - Always BUY, never short-sell
  - Inverse ETF on SELL signal, skip if no inverse
  - HOLD = upside breakout only
  - Dynamic sleeptime (2M ORB / 5M default)
  - after_market_closes() with delay for FINAL signals
  - Earnings filter, sizing guards, breakout filter
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
from strategies.leverage_map import get_leveraged_pair, is_direct_trade
from strategies.ai_engine import (
    check_ollama_available, grade_setup,
    detect_regime, get_cached_regime, narrate_trade,
)
from strategies.trade_journal import TradeJournal
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

# Default cache paths — overridden via parameters for dual-account mode
_DEFAULT_BIAS_CACHE    = "cache/daily_bias.json"
_DEFAULT_BIAS_BACKTEST = "cache/daily_bias_backtest.json"
_DEFAULT_JOURNAL_DB    = "cache/trade_journal.db"

SYMBOLS_FILE = "symbols.txt"

SIGNAL_PRELIM_HOUR   = 15
SIGNAL_PRELIM_MINUTE = 50

MARKET_OPEN_TIME  = dtime(9, 30)
MARKET_CLOSE_TIME = dtime(16, 25)
ORB_ENTRY_START   = dtime(9, 45)
ORB_ENTRY_END     = dtime(12, 0)

LEVERAGED_TICKERS = {
    "TQQQ","SQQQ","SPXL","SPXS","SOXL","SOXS","NVDL","NVDD",
    "UGL","GLL","AGQ","ZSL","JNUG","JDST","BITX","BITI","FAS","FAZ",
    "ERX","ERY","TSMU","PTIR","BITU","UCO","SCO","UPRO","SPXU",
}


def is_leveraged(ticker: str) -> bool:
    return ticker.upper() in LEVERAGED_TICKERS


class TrendFilteredORB(Strategy):

    parameters = {
        # ── Per-instance paths (set by run_live_combined.py for dual-account) ─
        "bias_cache_path":   _DEFAULT_BIAS_CACHE,
        "journal_db_path":   _DEFAULT_JOURNAL_DB,
        "log_file_path":     None,   # None = use root logger only

        # ── Iteration timing ──────────────────────────────────────────────────
        "sleeptime_orb":             "2M",
        "sleeptime_default":         "5M",
        "after_close_delay_minutes": 5,

        # ── Strategy ──────────────────────────────────────────────────────────
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

        # ── Swing mode ────────────────────────────────────────────────────────
        "swing_mode":                       False,
        "swing_min_conviction":             75,
        "swing_sell_cooldown_days":         90,
        "swing_force_sell_conviction":      85,
        "swing_force_sell_bear_score":      5,
    }

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def initialize(self):
        self.sleeptime = self.parameters.get("sleeptime_default", "5M")
        self.set_market("NYSE")

        self._name = self.name or "BOT"

        # ── Per-instance file logger ──────────────────────────────────────
        # If log_file_path is set, add a dedicated FileHandler for this
        # instance so its trades are easy to read in isolation.
        log_file = self.parameters.get("log_file_path")
        if log_file:
            os.makedirs(os.path.dirname(log_file) if os.path.dirname(log_file) else "logs", exist_ok=True)
            h = logging.FileHandler(log_file, encoding="utf-8")
            h.setFormatter(logging.Formatter(
                f"%(asctime)s | {self._name} | %(levelname)s | %(message)s"
            ))
            logging.getLogger().addHandler(h)

        # ── Per-instance state ────────────────────────────────────────────
        self._starting_capital     = self.portfolio_value
        self._last_date            = None
        self._prelim_signals_done  = False
        self._final_signals_done   = False
        self._market_opened_today  = False
        self._regime_checked_at    = None
        self._in_orb_window        = False

        self._orb_state  = {}
        self._positions  = {}
        self._trade_ids  = {}
        self._last_swing_sell: dict = {}

        self._daily_bias = self._load_bias()

        # ── Per-instance journal (separate DB per account) ────────────────
        journal_path = self.parameters.get("journal_db_path", _DEFAULT_JOURNAL_DB)
        self._journal = TradeJournal(db_path=journal_path)

        # ── Ollama warmup — only done once across all instances ───────────
        # ai_engine._ollama_available is a module-level flag; if the first
        # strategy instance already warmed up Ollama, the second will skip.
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            self.log_message("BACKTEST MODE — AI/regime skipped")
        else:
            try:
                available = check_ollama_available()
                if available:
                    self.log_message("Ollama ready — AI grading active")
                else:
                    self.log_message(
                        "⚠️  Ollama unavailable — fallback confidence (0.5x size)"
                    )
            except Exception as e:
                self.log_message(f"Ollama warmup error: {e}")

        swing_mode = self.parameters.get("swing_mode", False)
        self.log_message(
            f"[{self._name}] Initialized | "
            f"bias: {len(self._daily_bias)} symbols | "
            f"portfolio: ${self.portfolio_value:,.2f} | "
            f"swing_mode: {'ON' if swing_mode else 'off'} | "
            f"cache: {self.parameters['bias_cache_path']}"
        )

    def startup_refresh(self):
        """Called from run_live_combined.py before trader.run_all()."""
        tag = f"[{self._name}]"
        print(f"{tag} Refreshing bias signals...")
        try:
            self._run_eod_signals(label="STARTUP")
        except Exception as e:
            print(f"{tag} Bias refresh failed: {e} — using cached bias")

        print(f"{tag} Pre-warming earnings cache...")
        try:
            from strategies.earnings_filter import prefetch_earnings
            symbols = self._load_symbols()
            prefetch_earnings(symbols)
            print(f"{tag} Earnings cache ready for {len(symbols)} symbols")
        except Exception as e:
            print(f"{tag} Earnings pre-fetch skipped: {e}")

        print(f"{tag} Ready | bias: {len(self._daily_bias)} symbols\n")

        if _PREMARKET_AVAILABLE:
            try:
                trigger_sentiment_async(
                    self._load_symbols(),
                    bias=self._daily_bias,
                )
            except Exception as e:
                print(f"{tag} Sentiment trigger failed: {e}")

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
            self.log_message(
                f"[{self._name}] Bias from {bias_date or 'empty'} — refreshing for {today_str}"
            )
            self._run_eod_signals(label="PRE-MARKET")
        else:
            self.log_message(
                f"[{self._name}] Bias current ({bias_date}, {len(self._daily_bias)} symbols)"
            )

        if _PREMARKET_AVAILABLE and not os.getenv("LUMIBOT_BACKTEST_MODE", ""):
            try:
                api_key    = os.getenv(f"ALPACA_API_KEY_{self._name}",    os.getenv("ALPACA_API_KEY", ""))
                api_secret = os.getenv(f"ALPACA_API_SECRET_{self._name}", os.getenv("ALPACA_API_SECRET", ""))
                self._daily_bias = enrich_bias(
                    self._daily_bias, api_key, api_secret, run_sentiment=True
                )
                self._save_bias(self._daily_bias)
                gap_ups   = sum(1 for v in self._daily_bias.values() if v.get("gap_signal") == "GAP_UP")
                gap_downs = sum(1 for v in self._daily_bias.values() if v.get("gap_signal") == "GAP_DOWN")
                self.log_message(
                    f"[{self._name}] Pre-market enrichment | gaps: {gap_ups} up / {gap_downs} down"
                )
            except Exception as e:
                self.log_message(f"[{self._name}] Pre-market enrichment failed: {e}")

    def after_market_closes(self):
        """FINAL EOD signals with delay for closing prices to settle."""
        if self._final_signals_done:
            return
        delay = self.parameters.get("after_close_delay_minutes", 5)
        if delay > 0:
            self.log_message(
                f"[{self._name}] After-close — waiting {delay} min for prices to settle..."
            )
            time.sleep(delay * 60)
        self.log_message(f"[{self._name}] Running FINAL EOD signals")
        try:
            self._run_eod_signals(label="FINAL")
            self._final_signals_done = True
            self._check_and_close_sell_signals(reason="FINAL_SELL_SIGNAL")
        except Exception as e:
            self.log_message(f"[{self._name}] FINAL signals error: {e}")

    # ── Main iteration ────────────────────────────────────────────────────

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

        is_weekday = now.weekday() < 5
        in_session = MARKET_OPEN_TIME <= now.time() <= MARKET_CLOSE_TIME
        if not is_weekday or not in_session:
            return

        if not self._market_opened_today:
            self._sync_positions_from_broker()
            self._market_opened_today = True
            self.log_message(
                f"[{self._name}] Market open | {len(self._positions)} positions carried"
            )

        # ── Dynamic sleeptime ─────────────────────────────────────────────
        in_orb = ORB_ENTRY_START <= now.time() <= ORB_ENTRY_END
        if in_orb and not self._in_orb_window:
            self.sleeptime = self.parameters.get("sleeptime_orb", "2M")
            self._in_orb_window = True
            self.log_message(
                f"[{self._name}] ORB window open — {self.parameters['sleeptime_orb']} iterations"
            )
        elif not in_orb and self._in_orb_window:
            self.sleeptime = self.parameters.get("sleeptime_default", "5M")
            self._in_orb_window = False
            self.log_message(
                f"[{self._name}] ORB window closed — {self.parameters['sleeptime_default']} iterations"
            )

        eod_h, eod_m = map(int, self.parameters["eod_exit_time"].split(":"))
        if now.time() >= dtime(eod_h, eod_m):
            self._close_leveraged_positions("EOD")

        if (now.time() >= dtime(SIGNAL_PRELIM_HOUR, SIGNAL_PRELIM_MINUTE)
                and not self._prelim_signals_done):
            self.log_message(f"[{self._name}] 3:50 PM — PRELIM EOD signals")
            self._run_eod_signals(label="PRELIM")
            self._prelim_signals_done = True
            self._check_and_close_sell_signals(reason="PRELIM_SELL_SIGNAL")

        if now.time() >= dtime(eod_h, eod_m):
            return

        self._check_and_close_sell_signals(reason="SELL_SIGNAL")

        if (os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() != "true" and
                (self._regime_checked_at is None or
                 (now - self._regime_checked_at).seconds >= 1800)):
            self._refresh_regime("QQQ")
            self._regime_checked_at = now

        self._monitor_open_positions()

        if in_orb:
            max_pos    = self.parameters["max_positions"]
            slots_free = max_pos - len(self._positions)

            if slots_free > 0:
                candidates = []
                for symbol in self._load_symbols():
                    try:
                        c = self._process_symbol(symbol, now, today)
                        if c is not None:
                            candidates.append(c)
                    except Exception as e:
                        self.log_message(f"[{self._name}] Error {symbol}: {e}")

                if candidates:
                    candidates.sort(key=lambda x: x["conviction"], reverse=True)
                    ranked_log = ", ".join(
                        f"{c['symbol']}({c['conviction']:.0f})" for c in candidates
                    )
                    self.log_message(
                        f"[{self._name}] Candidates [{len(candidates)}] ranked: {ranked_log} | "
                        f"slots free: {slots_free}/{max_pos}"
                    )
                    executed = 0
                    for c in candidates:
                        if executed >= slots_free or len(self._positions) >= max_pos:
                            self.log_message(
                                f"[{self._name}] SKIP {c['symbol']} — max_positions reached"
                            )
                            continue
                        self._execute_candidate(c)
                        executed += 1

    # ── Broker Position Sync ──────────────────────────────────────────────

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
                            f"[{self._name}] Restored {ticker}: "
                            f"stop={stop:.2f} target={target:.2f}"
                        )
                    else:
                        self.log_message(
                            f"[{self._name}] No journal for {ticker} — "
                            f"default stop={stop:.2f} target={target:.2f}"
                        )
                except Exception:
                    pass

                self._positions[ticker] = {
                    "symbol": ticker, "signal_symbol": ticker,
                    "exec_ticker": ticker, "is_inverse": False,
                    "direction": "LONG",
                    "entry_price": avg_price, "stop": stop, "target": target,
                    "qty": qty, "entry_value": self.portfolio_value,
                    "overnight_ok": overnight, "synced": True,
                }
                synced.append(ticker)
                if is_leveraged(ticker):
                    self.log_message(f"[{self._name}] ⚠️  {ticker} leveraged — closes at EOD")
            if synced:
                self.log_message(f"[{self._name}] Synced from Alpaca: {synced}")
        except Exception as e:
            self.log_message(f"[{self._name}] Position sync failed: {e}")

    # ── Sell Signal Exit ──────────────────────────────────────────────────

    def _check_and_close_sell_signals(self, reason: str = "SELL_SIGNAL"):
        swing_mode      = self.parameters.get("swing_mode", False)
        cooldown_days   = self.parameters.get("swing_sell_cooldown_days", 90)
        force_conv_gate = self.parameters.get("swing_force_sell_conviction", 85)
        force_bear_gate = self.parameters.get("swing_force_sell_bear_score", 5)

        for exec_ticker, pos in list(self._positions.items()):
            if not pos.get("overnight_ok", False):
                continue

            signal_symbol = pos.get("signal_symbol", pos.get("symbol", exec_ticker))
            bias          = self._daily_bias.get(signal_symbol, {})
            action        = bias.get("action", "HOLD")
            is_inverse    = pos.get("is_inverse", False)

            if is_inverse:
                should_close = action in ("BUY", "STRONG_BUY")
                close_reason = f"underlying {signal_symbol} now BUY — closing inverse"
            else:
                should_close = action in ("SELL", "STRONG_SELL")
                close_reason = f"{signal_symbol} → {action}"

            if not should_close:
                continue

            if swing_mode and not is_inverse:
                last_sell  = self._last_swing_sell.get(signal_symbol)
                days_since = (ddate.today() - last_sell).days if last_sell else 999
                bear_score   = bias.get("bear_score", 0)
                vol_ratio    = bias.get("vol_ratio", 1.0)
                action_bonus = 10 if action == "STRONG_SELL" else 0
                conviction   = (
                    bias.get("ai_confidence", 0.60) * 40
                    + bear_score * 8
                    + min(vol_ratio - 1.0, 1.0) * 10
                    + action_bonus
                )
                within_cooldown = days_since < cooldown_days
                is_force_sell   = (
                    conviction >= force_conv_gate
                    and bear_score >= force_bear_gate
                    and action == "STRONG_SELL"
                )

                if within_cooldown and not is_force_sell:
                    self.log_message(
                        f"[{self._name}][SWING] 🔒 Holding {signal_symbol} — "
                        f"{days_since}d since last sell (cooldown={cooldown_days}d)"
                    )
                    continue

                if within_cooldown and is_force_sell:
                    self.log_message(
                        f"[{self._name}][SWING] ⚡ FORCE-SELL {signal_symbol}"
                    )
                else:
                    self.log_message(
                        f"[{self._name}][SWING] 📅 Routine sell {signal_symbol} — "
                        f"{days_since}d >= {cooldown_days}d"
                    )
                self._last_swing_sell[signal_symbol] = ddate.today()
            else:
                self.log_message(
                    f"[{self._name}] Signal exit [{reason}] | "
                    f"{exec_ticker} — {close_reason}"
                )

            try:
                bars = self.get_historical_prices(exec_ticker, 2, "5m")
                exit_price = (float(bars.df["close"].iloc[-1])
                              if bars and len(bars.df) > 0
                              else pos["entry_price"])
            except Exception:
                exit_price = pos["entry_price"]

            self._close_single_position(exec_ticker, pos, reason, exit_price)

    # ── Stop/Target Monitor ───────────────────────────────────────────────

    def _monitor_open_positions(self):
        for exec_ticker, pos in list(self._positions.items()):
            try:
                bars = self.get_historical_prices(exec_ticker, 2, "5m")
                if bars is None or len(bars.df) == 0:
                    continue
                current = float(bars.df["close"].iloc[-1])
                hit = None
                if current <= pos["stop"]:    hit = "STOP"
                elif current >= pos["target"]: hit = "TARGET"
                if hit:
                    self._close_single_position(exec_ticker, pos, hit, current)
            except Exception as e:
                self.log_message(f"[{self._name}] Monitor error {exec_ticker}: {e}")

    def _close_single_position(self, exec_ticker: str, pos: dict,
                                reason: str, exit_price: float):
        try:
            position = self.get_position(exec_ticker)
            if position and int(position.quantity) > 0:
                self.submit_order(
                    self.create_order(exec_ticker, int(position.quantity), "sell")
                )
        except Exception as e:
            self.log_message(f"[{self._name}] Close order failed {exec_ticker}: {e}")
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

        inv_tag = " [inverse]" if pos.get("is_inverse") else ""
        o_tag   = "overnight" if pos.get("overnight_ok") else "intraday"
        msg     = (
            f"[{self._name}] CLOSED {exec_ticker}{inv_tag} ({reason}) "
            f"@ {exit_price:.2f} | PnL: ${pnl:+.2f} | {o_tag}"
        )
        self.log_message(msg)
        self._notify(f"[{self._name}] EXIT {exec_ticker}", msg)

        self._positions.pop(exec_ticker, None)
        self._trade_ids.pop(exec_ticker, None)
        signal_symbol = pos.get("signal_symbol", pos.get("symbol"))
        if signal_symbol and signal_symbol in self._orb_state:
            self._orb_state[signal_symbol]["trade_taken"] = False

    # ── EOD: Close Leveraged/Inverse ──────────────────────────────────────

    def _close_leveraged_positions(self, reason: str):
        to_close = {
            k: v for k, v in self._positions.items()
            if is_leveraged(k) or not v.get("overnight_ok", True)
        }
        if not to_close:
            overnight = [k for k in self._positions if not is_leveraged(k)]
            if overnight:
                self.log_message(f"[{self._name}] EOD: keeping overnight: {overnight}")
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
            self.log_message(f"[{self._name}] EOD done | holding overnight: {overnight}")

    # ── EOD Signal Runner ─────────────────────────────────────────────────

    def _run_eod_signals(self, label: str = "EOD"):
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            self._run_eod_signals_backtest(label)
            return

        api_key    = os.getenv(f"ALPACA_API_KEY_{self._name}",    os.getenv("ALPACA_API_KEY", ""))
        secret_key = os.getenv(f"ALPACA_API_SECRET_{self._name}", os.getenv("ALPACA_API_SECRET", ""))
        symbols    = self._load_symbols()

        self.log_message(f"[{self._name}][{label}] Running signals for {len(symbols)} symbols...")
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
                self.log_message(f"[{self._name}][{label}] Signal error {symbol}: {e}")
                new_bias[symbol] = {
                    "action": "HOLD",
                    "date":   str(datetime.now().date()),
                    "source": label,
                }

        self._daily_bias = new_bias
        self._save_bias(new_bias)

        summary = (
            f"[{self._name}][{label}] Signals | "
            f"BUY:{len(buys)} SELL:{len(sells)} "
            f"HOLD:{len(symbols)-len(buys)-len(sells)}\n"
            f"Buys:  {', '.join(buys[:12])}\n"
            f"Sells: {', '.join(sells[:12])}"
        )
        self.log_message(summary)
        self._notify(f"[{self._name}] [{label}] EOD Signals", summary)

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

        if not avail_symbols:
            self.log_message(f"[{self._name}][{label}][BACKTEST] No data — skipping")
            return

        new_bias = {}
        buys, sells = [], []

        for symbol in avail_symbols:
            try:
                bars = self.get_historical_prices(symbol, 100, "5m")
                if bars is None or len(bars.df) < 10:
                    new_bias[symbol] = {
                        "action": "HOLD", "bull_score": 0, "bear_score": 0,
                        "rsi": 50.0, "vol_ratio": 1.0,
                        "date": str(self.get_datetime().date()), "source": label,
                    }
                    continue

                close  = bars.df["close"].dropna()
                volume = bars.df["volume"].dropna()

                ema2   = close.ewm(span=2,  adjust=False).mean()
                ema3   = close.ewm(span=3,  adjust=False).mean()
                ema5   = close.ewm(span=5,  adjust=False).mean()
                sma50  = close.rolling(min(50,  len(close))).mean()
                sma200 = close.rolling(min(200, len(close))).mean()

                delta = close.diff()
                gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
                loss  = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
                rsi   = float((100 - 100 / (1 + gain / loss.replace(0, 1e-10))).iloc[-1])

                ema12     = close.ewm(span=12, adjust=False).mean()
                ema26     = close.ewm(span=26, adjust=False).mean()
                macd_hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()

                price      = float(close.iloc[-1])
                bull_score = sum([
                    float(ema2.iloc[-1]) > float(ema3.iloc[-1]),
                    float(ema3.iloc[-1]) > float(ema5.iloc[-1]),
                    price > float(sma50.iloc[-1]),
                    price > float(sma200.iloc[-1]),
                    rsi < 62,
                    float(macd_hist.iloc[-1]) > 0,
                ])
                bear_score = sum([
                    float(ema2.iloc[-1]) < float(ema3.iloc[-1]),
                    float(ema3.iloc[-1]) < float(ema5.iloc[-1]),
                    price < float(sma50.iloc[-1]),
                    price < float(sma200.iloc[-1]),
                    rsi > 38,
                    float(macd_hist.iloc[-1]) < 0,
                ])

                vol_ratio = 1.0
                if len(volume) >= 20:
                    avg_vol = float(volume.rolling(20).mean().iloc[-1])
                    if avg_vol > 0:
                        vol_ratio = float(volume.iloc[-1]) / avg_vol

                if bull_score >= 5 and rsi < 68 and vol_ratio > 1.1:
                    action = "STRONG_BUY"
                elif bull_score >= 4 and rsi < 62:
                    action = "BUY"
                elif bear_score >= 5 and rsi > 32 and vol_ratio > 1.1:
                    action = "STRONG_SELL"
                elif bear_score >= 4 and rsi > 38:
                    action = "SELL"
                else:
                    action = "HOLD"

                new_bias[symbol] = {
                    "action": action, "bull_score": int(bull_score),
                    "bear_score": int(bear_score), "rsi": round(rsi, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "date": str(self.get_datetime().date()), "source": label,
                }
                if action in ("BUY", "STRONG_BUY"):     buys.append(symbol)
                elif action in ("SELL", "STRONG_SELL"):  sells.append(symbol)

            except Exception as e:
                self.log_message(f"[{self._name}][{label}][BACKTEST] {symbol}: {e}")
                new_bias[symbol] = {
                    "action": "HOLD", "bull_score": 0, "bear_score": 0,
                    "rsi": 50.0, "vol_ratio": 1.0,
                    "date": str(self.get_datetime().date()), "source": label,
                }

        self._daily_bias = new_bias
        self._save_bias(new_bias)
        self.log_message(
            f"[{self._name}][{label}][BACKTEST] Done | "
            f"BUY:{len(buys)} SELL:{len(sells)} "
            f"HOLD:{len(avail_symbols)-len(buys)-len(sells)}"
        )

    # ── Regime Refresh ────────────────────────────────────────────────────

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
                f"[{self._name}] Regime [{symbol}]: {regime.get('regime')} "
                f"({regime.get('orb_suitability')}) conf={regime.get('confidence',0):.2f}"
            )
        except Exception as e:
            self.log_message(f"[{self._name}] Regime refresh failed: {e}")

    # ── Per-Symbol ORB Entry ──────────────────────────────────────────────

    def _process_symbol(self, symbol: str, now, today):
        """
        Same trade model as v9 — always BUY, never short-sell.
        BUY signal  → BUY bull ETF
        SELL signal → BUY inverse ETF (skip if no inverse)
        HOLD signal → BUY bull ETF on upside breakout only
        """
        bias   = self._daily_bias.get(symbol, {"action": "HOLD"})
        action = bias.get("action", "HOLD")

        hold_bias = (action == "HOLD")
        want_bull = action in ("BUY", "STRONG_BUY")
        want_bear = action in ("SELL", "STRONG_SELL")

        pair      = get_leveraged_pair(symbol)
        direct    = is_direct_trade(symbol)
        swing_mode_on = self.parameters.get("swing_mode", False)

        # ── Swing mode early guards ────────────────────────────────────────
        # In swing mode we only hold direct-trade unleveraged long positions.
        # Block any symbol whose bull ETF is leveraged (e.g. QQQ→TQQQ) —
        # swing mode uses the underlying directly via get_swing_ticker(), but
        # we still skip SELL/STRONG_SELL entirely since we never go bearish
        # in swing mode (no leveraged ETFs, no inverse ETFs).
        if swing_mode_on:
            if want_bear:
                # Never trade bearish in swing mode — no inverse ETFs ever
                return None
            # Also block symbols where even the direct ticker is a
            # leveraged/inverse product (e.g. TQQQ, SQQQ in symbols.txt)
            from strategies.leverage_map import is_leveraged_or_inverse
            if is_leveraged_or_inverse(symbol):
                return None

        if want_bear and direct:
            return None
        if not want_bull and not hold_bias and not want_bear:
            return None

        try:
            from strategies.earnings_filter import is_earnings_safe, get_earnings_info
            if not is_earnings_safe(symbol):
                info = get_earnings_info(symbol)
                self.log_message(
                    f"[{self._name}] SKIP {symbol} — earnings in {info.get('hours_until','?')}h"
                )
                return None
        except Exception:
            pass

        if symbol not in self._orb_state:
            self._orb_state[symbol] = {
                "or_high": None, "or_low": None, "or_mid": None,
                "or_established": False, "trade_taken": False,
            }
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

        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            regime = {"regime": "trending", "confidence": 0.5,
                      "orb_suitability": "moderate",
                      "stop_adjustment": 1.0, "target_adjustment": 1.0}
            regime_type = "trending"; regime_conf = 0.5
            orb_suitability = "moderate"; stop_adj = 1.0; target_adj = 1.0
        else:
            regime = get_cached_regime(symbol)
            if regime.get("regime") == "unknown":
                regime = get_cached_regime("QQQ")
            regime_type     = regime.get("regime", "unknown")
            regime_conf     = regime.get("confidence", 0.5)
            orb_suitability = regime.get("orb_suitability", "moderate")
            stop_adj        = regime.get("stop_adjustment", 1.0)
            target_adj      = regime.get("target_adjustment", 1.0)

            if regime_type == "low_liquidity" and regime_conf >= 0.70:
                self.log_message(f"[{self._name}] SKIP {symbol} — low liquidity")
                return None

        if orb_suitability == "poor" and regime_conf >= 0.70:
            self.log_message(f"[{self._name}] SKIP {symbol} — poor ORB regime")
            return None

        min_bp         = self.parameters.get("min_breakout_pct", 0.001)
        is_long_break  = current > state["or_high"] * (1 + min_bp)
        is_short_break = current < state["or_low"]  * (1 - min_bp)

        exec_ticker = None
        is_inverse  = False

        if want_bull:
            if not is_long_break:
                return None
            exec_ticker = pair["bull"]
            is_inverse  = False
        elif want_bear:
            if not is_short_break:
                return None
            exec_ticker = pair["bear"]
            is_inverse  = True
        elif hold_bias:
            if not is_long_break:
                return None
            exec_ticker = pair["bull"]
            is_inverse  = False

        if exec_ticker is None:
            return None

        if exec_ticker in self._positions:
            return None
        if is_inverse and pair["bull"] in self._positions:
            return None
        if not is_inverse and pair["bear"] in self._positions:
            return None

        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            grading = {"approve": True, "confidence": 0.7, "size_multiplier": 1.0}
        else:
            candles = [{"o": r["open"], "h": r["high"],
                         "l": r["low"],  "c": r["close"], "v": r["volume"]}
                       for _, r in df_today.iterrows()]
            avg_vol = float(df_today["volume"].mean()) if not df_today.empty else 1.0
            grading = grade_setup(
                symbol=symbol,
                direction="SHORT" if is_inverse else "LONG",
                candles=candles,
                or_high=state["or_high"], or_low=state["or_low"],
                current_price=current, avg_volume=avg_vol,
            )

        ai_min = self.parameters.get("ai_min_confidence", 0.55)
        if grading["confidence"] < ai_min or not grading.get("approve", True):
            self.log_message(
                f"[{self._name}] SKIP {symbol} — AI {grading['confidence']:.2f}"
            )
            return None

        if swing_mode_on:
            # Bearish trades already blocked at top of function.
            # Redirect exec_ticker to the unleveraged underlying.
            # e.g. QQQ BUY → exec_ticker was TQQQ → redirect to QQQ
            from strategies.leverage_map import get_swing_ticker, is_leveraged_or_inverse
            swing_ticker = get_swing_ticker(symbol)
            if is_leveraged_or_inverse(swing_ticker):
                # Safety net — should never fire given the early guard above
                self.log_message(
                    f"[{self._name}][SWING] ⚠️ Safety block — {swing_ticker} is leveraged"
                )
                return None
            exec_ticker = swing_ticker
            swing_min_conv     = self.parameters.get("swing_min_conviction", 75)
            preview_conviction = (
                grading.get("confidence", 0.60) * 40
                + bias.get("bull_score", 0) * 8
                + min(bias.get("vol_ratio", 1.0) - 1.0, 1.0) * 10
                + (10 if action in ("STRONG_BUY", "STRONG_SELL") else 0)
            )
            if preview_conviction < swing_min_conv:
                self.log_message(
                    f"[{self._name}][SWING] Skip {symbol} — "
                    f"conviction {preview_conviction:.0f} < {swing_min_conv}"
                )
                return None

        base_risk    = self.parameters["risk_pct"]
        size_mult    = grading.get("size_multiplier", 1.0)
        if hold_bias:
            size_mult *= self.parameters["hold_override_size"]
        if regime_type == "volatile":
            size_mult *= 0.75
        effective_risk = min(base_risk * size_mult, 0.02)

        risk_dist     = abs(current - state["or_mid"]) * stop_adj
        min_stop_pct  = self.parameters.get("min_stop_pct", 0.005)
        risk_dist     = max(risk_dist, current * min_stop_pct)

        if risk_dist <= 0:
            return None

        initial_stop   = current - risk_dist
        initial_target = current + risk_dist * self.parameters["reward_ratio"] * target_adj

        qty           = int((self.portfolio_value * effective_risk) / risk_dist)
        max_pos_value = self.portfolio_value * self.parameters.get("max_position_pct", 0.15)
        qty           = min(qty, int(max_pos_value / max(current, 0.01)))

        if qty < 1:
            return None

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
            "is_inverse": is_inverse, "trade_type": trade_type,
            "current": current, "qty": qty,
            "initial_stop": initial_stop, "initial_target": initial_target,
            "effective_risk": effective_risk, "size_mult": size_mult,
            "hold_bias": hold_bias, "conviction": conviction,
            "grading": grading, "regime": regime, "regime_type": regime_type,
            "orb_suitability": orb_suitability, "stop_adj": stop_adj,
            "target_adj": target_adj, "bias": bias, "action": action,
            "state": state, "direct": direct, "df_today": df_today,
        }

    def _execute_candidate(self, c: dict):
        symbol      = c["symbol"]
        exec_ticker = c["exec_ticker"]
        is_inverse  = c["is_inverse"]
        trade_type  = c["trade_type"]
        current     = c["current"]
        qty         = c["qty"]
        grading     = c["grading"]
        regime      = c["regime"]
        bias        = c["bias"]
        action      = c["action"]
        state       = c["state"]
        direct      = c["direct"]

        try:
            order = self.create_order(exec_ticker, qty, "buy", time_in_force="day")
            self.submit_order(order)
        except Exception as e:
            self.log_message(f"[{self._name}] Order failed {exec_ticker}: {e}")
            return

        overnight_eligible = direct and not is_inverse

        trade_id = self._journal.open_trade(
            symbol=symbol, exec_ticker=exec_ticker,
            direction="LONG" if not is_inverse else "LONG_INVERSE",
            entry_price=current, quantity=qty,
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

        self._positions[exec_ticker] = {
            "symbol": symbol, "signal_symbol": symbol,
            "exec_ticker": exec_ticker, "is_inverse": is_inverse,
            "direction": "LONG",
            "entry_price": current, "stop": c["initial_stop"],
            "target": c["initial_target"], "qty": qty,
            "entry_value": self.portfolio_value, "overnight_ok": overnight_eligible,
        }
        self._trade_ids[exec_ticker] = trade_id
        state["trade_taken"] = True

        o_tag     = " [OVERNIGHT]" if overnight_eligible else " [EOD]"
        swing_tag = " [SWING]" if self.parameters.get("swing_mode", False) else ""
        inv_tag   = " → buying inverse ETF" if is_inverse else ""
        msg = (
            f"[{self._name}] {trade_type}{swing_tag} | {symbol}→{exec_ticker} x{qty} "
            f"@ {current:.2f} | Stop:{c['initial_stop']:.2f} "
            f"Target:{c['initial_target']:.2f} | "
            f"AI:{grading['confidence']:.2f}({c['size_mult']:.1f}x) | "
            f"Conviction:{c['conviction']:.0f} | "
            f"Regime:{regime.get('regime','?')}{inv_tag}{o_tag}"
        )
        self.log_message(msg)
        self._notify(f"[{self._name}] BUY {exec_ticker} ({trade_type})", msg)

    # ── Helpers ───────────────────────────────────────────────────────────

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
        try:
            with open(SYMBOLS_FILE, "r") as f:
                return [l.strip().upper() for l in f
                        if l.strip() and not l.startswith("#")]
        except FileNotFoundError:
            return ["QQQ"]

    def _bias_path(self) -> str:
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            return _DEFAULT_BIAS_BACKTEST
        return self.parameters.get("bias_cache_path", _DEFAULT_BIAS_CACHE)

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
            self.log_message(f"[{self._name}] Bias save failed: {e}")

    # ── Shutdown ──────────────────────────────────────────────────────────

    def on_strategy_end(self):
        self._close_leveraged_positions("STRATEGY_END")
        try:
            stats = self._journal.get_stats(days=30)
            self.log_message(
                f"[{self._name}] 30-day: {stats.get('total_trades',0)} trades | "
                f"WR:{stats.get('win_rate',0)}% | "
                f"PnL:${stats.get('total_pnl',0):+.2f} | "
                f"PF:{stats.get('profit_factor',0):.2f}"
            )
            self._journal.export_csv()
        except Exception as e:
            self.log_message(f"[{self._name}] Stats error: {e}")

    def on_abrupt_closing(self):
        self.sell_all()