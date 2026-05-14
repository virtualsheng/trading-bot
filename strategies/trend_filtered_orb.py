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
"""

import os
import json
import pandas as pd
import numpy as np
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


SYMBOLS_FILE        = "symbols.txt"
BIAS_CACHE          = "cache/daily_bias.json"
BIAS_CACHE_BACKTEST = "cache/daily_bias_backtest.json"

# ── Signal timing ─────────────────────────────────────────────────────────
# 3:50 PM — preliminary EOD signals (market still open, ~10 min early)
#            acts on SELL signals immediately while market is open
SIGNAL_PRELIM_HOUR   = 15
SIGNAL_PRELIM_MINUTE = 50

# 4:15 PM — final EOD signals using official closing prices
#            overwrites preliminary cache, acts on any new SELL signals
SIGNAL_FINAL_HOUR    = 16
SIGNAL_FINAL_MINUTE  = 15

# Market session window — iteration returns immediately outside these bounds
MARKET_OPEN_TIME  = dtime(9, 30)
MARKET_CLOSE_TIME = dtime(16, 25)   # 4:25 PM gives buffer for 4:15 final run

LEVERAGED_TICKERS = {
    "TQQQ","SQQQ","SPXL","SPXS","SOXL","SOXS","NVDL","NVDD",
    "UGL","GLL","AGQ","ZSL","JNUG","JDST","BITX","FAS","FAZ",
    "ERX","ERY","TSMU","PTIR","BITU","UCO","SCO","UPRO","SPXU",
}


def is_leveraged(ticker: str) -> bool:
    return ticker.upper() in LEVERAGED_TICKERS


class TrendFilteredORB(Strategy):

    parameters = {
        "orb_minutes":        15,
        "bar_minutes":        5,
        "risk_pct":           0.01,
        "reward_ratio":       2.0,
        "eod_exit_time":      "15:45",
        "max_positions":      5,
        "ai_min_confidence":  0.55,
        "hold_override":      False,
        "hold_override_size": 0.5,
        # Swing mode defaults (overridden by run_live_combined.py PARAMS)
        "swing_mode":                  False,
        "swing_min_conviction":        75,
        "swing_sell_cooldown_days":    90,
        "swing_force_sell_conviction": 85,
        "swing_force_sell_bear_score": 5,
    }

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def initialize(self):
        self.sleeptime = "5M"
        self.set_market("NYSE")

        self._starting_capital     = self.portfolio_value
        self._last_date            = None
        self._prelim_signals_done  = False   # 3:50 PM run
        self._final_signals_done   = False   # 4:15 PM run
        self._market_opened_today  = False
        self._regime_checked_at    = None

        self._orb_state  = {}
        self._positions  = {}
        self._trade_ids  = {}

        # Swing mode: tracks last sell date per symbol to enforce
        # the quarterly cooldown (swing_sell_cooldown_days).
        # Key: signal symbol (e.g. "QQQ"), Value: datetime.date of last sell.
        self._last_swing_sell: dict = {}

        self._daily_bias = self._load_bias()
        self._journal    = TradeJournal()

        # ── Warm up Ollama (skipped in backtest mode) ─────────────────────
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            self.log_message("BACKTEST MODE — AI/regime skipped for speed")
        else:
            try:
                available = check_ollama_available()
                if available:
                    self.log_message("Ollama ready — AI grading active")
                else:
                    self.log_message(
                        "⚠️  Ollama unavailable — trades will use fallback "
                        "confidence (0.5x size). Run: ollama serve"
                    )
            except Exception as e:
                self.log_message(f"Ollama warmup error: {e}")

        swing_mode = self.parameters.get("swing_mode", False)
        self.log_message(
            f"Initialized | bias: {len(self._daily_bias)} symbols | "
            f"portfolio: ${self.portfolio_value:,.2f} | "
            f"swing_mode: {'ON' if swing_mode else 'off'}"
        )

    def startup_refresh(self):
        """
        Called from run_live_combined.py immediately after the strategy is
        instantiated — before trader.run_all(). Runs everything that benefits
        from being fresh at startup rather than waiting until market open:

          1. Bias signals — refreshes daily_bias.json right now so the cache
             is never stale when the first ORB window opens, regardless of
             what time the script is started.
          2. Earnings cache — pre-fetches the earnings calendar for all symbols
             so the first trade check doesn't incur the fetch latency.

        Regime is intentionally NOT pre-warmed here — the reading at 7 AM would
        be ~2.5 hours stale by 9:30 AM. before_market_opens() handles it closer
        to the open.
        """
        print("[startup] Refreshing bias signals...")
        try:
            self._run_eod_signals(label="STARTUP")
        except Exception as e:
            print(f"[startup] Bias refresh failed: {e} — using cached bias")

        print("[startup] Pre-warming earnings cache...")
        try:
            from strategies.earnings_filter import prefetch_earnings
            symbols = self._load_symbols()
            prefetch_earnings(symbols)
            print(f"[startup] Earnings cache ready for {len(symbols)} symbols")
        except Exception as e:
            print(f"[startup] Earnings pre-fetch skipped: {e} — will fetch on demand")

        print(f"[startup] Ready | bias: {len(self._daily_bias)} symbols\n")

        # ── Kick off Sentiment-Trading-Alpha pipeline in background ──────────────
        # Takes 30–120s to run LLM inference — fires async so it's ready
        # by the time the ORB window opens at 9:45 AM.
        if _PREMARKET_AVAILABLE:
            try:
                symbols = self._load_symbols()
                trigger_sentiment_async(symbols)
            except Exception as e:
                print(f"[startup] Sentiment trigger failed: {e}")

    def before_market_opens(self):
        """Called once before each market session (~9:00–9:15 AM ET)."""
        # Clear earnings cache for new day (fresh data, not startup cache)
        try:
            from strategies.earnings_filter import clear_cache
            clear_cache()
        except Exception:
            pass

        # Pre-warm regime close to open — more accurate than doing it at startup
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() != "true":
            self._refresh_regime("QQQ")
            self._regime_checked_at = self.get_datetime()

        # Only re-run signals if the bias is from a previous day.
        # If startup_refresh() already ran today, skip it.
        bias_date = next(iter(self._daily_bias.values()), {}).get("date") if self._daily_bias else None
        today_str = self.get_datetime().date().strftime("%Y-%m-%d")
        if not self._daily_bias or bias_date != today_str:
            self.log_message(
                f"Bias is from {bias_date or 'empty'} — refreshing for {today_str}"
            )
            self._run_eod_signals(label="PRE-MARKET")
        else:
            self.log_message(
                f"Bias current ({bias_date}, {len(self._daily_bias)} symbols) — skipping pre-market refresh"
            )

        # ── Pre-market enrichment ─────────────────────────────────────────
        # Runs at ~9:00–9:15 AM: gap analysis + Alpaca news sentiment.
        # Sentiment-Trading-Alpha was already triggered async at startup and will
        # be available from cache by now.
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
                    f"Pre-market enrichment complete | "
                    f"gaps: {gap_ups} up / {gap_downs} down"
                )
            except Exception as e:
                self.log_message(f"Pre-market enrichment failed: {e} — using technical bias only")

    # ── Main iteration ────────────────────────────────────────────────────

    def on_trading_iteration(self):
        now   = self.get_datetime()
        today = now.date()

        # ── Daily reset ────────────────────────────────────────────────────
        if today != self._last_date:
            self._last_date           = today
            self._prelim_signals_done = False
            self._final_signals_done  = False
            self._market_opened_today = False
            self._orb_state           = {}
            # Do NOT clear _positions — overnight positions carry forward

        # ── Market hours guard ─────────────────────────────────────────────
        # Nothing runs outside Mon–Fri 9:30 AM – 4:25 PM ET.
        # This eliminates all off-hours API calls and log noise.
        is_weekday = now.weekday() < 5   # Mon=0 … Fri=4
        in_session = MARKET_OPEN_TIME <= now.time() <= MARKET_CLOSE_TIME

        if not is_weekday or not in_session:
            return

        # ── Position sync at market open (once per day) ────────────────────
        if not self._market_opened_today:
            self._sync_positions_from_broker()
            self._market_opened_today = True
            self.log_message(
                f"Market open | {len(self._positions)} positions carried"
            )

        # ── 3:45 PM — close all leveraged ETFs ────────────────────────────
        eod_h, eod_m = map(int, self.parameters["eod_exit_time"].split(":"))
        if now.time() >= dtime(eod_h, eod_m):
            self._close_leveraged_positions("EOD")
            # Fall through — don't return yet, signals still need to run

        # ── 3:50 PM — preliminary EOD signals ─────────────────────────────
        # Market is still technically open. Prices are ~10 min pre-close.
        # After updating bias, immediately act on any SELL signals so we
        # can close overnight positions while the market is still open.
        if (now.time() >= dtime(SIGNAL_PRELIM_HOUR, SIGNAL_PRELIM_MINUTE)
                and not self._prelim_signals_done):
            self.log_message("3:50 PM — running preliminary EOD signals")
            self._run_eod_signals(label="PRELIM")
            self._prelim_signals_done = True
            # Immediately act on SELL signals from this fresh run
            self._check_and_close_sell_signals(reason="PRELIM_SELL_SIGNAL")

        # ── 4:15 PM — final EOD signals with official closing prices ───────
        # Market is closed. Official close prices are available.
        # Overwrites the preliminary cache with accurate final data.
        # Acts on any SELL signals that emerged at the actual close.
        if (now.time() >= dtime(SIGNAL_FINAL_HOUR, SIGNAL_FINAL_MINUTE)
                and not self._final_signals_done):
            self.log_message("4:15 PM — running FINAL EOD signals (official close)")
            self._run_eod_signals(label="FINAL")
            self._final_signals_done = True
            # Act on any SELL signals from the official close
            self._check_and_close_sell_signals(reason="FINAL_SELL_SIGNAL")
            return  # Nothing left to do after final signals

        # ── Skip everything below if market is past 3:45 PM ───────────────
        # Leveraged positions are closed. Only signals and overnight
        # position management still runs after 3:45.
        if now.time() >= dtime(eod_h, eod_m):
            return

        # ── Everything below only runs 9:30 AM – 3:45 PM ──────────────────

        # ── Check SELL signals on overnight positions ──────────────────────
        # Uses prior-day bias — acts if symbol flipped to SELL overnight
        self._check_and_close_sell_signals(reason="SELL_SIGNAL")

        # ── Regime refresh every 30 min (skipped in backtest mode) ──────────
        if (os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() != "true" and
                (self._regime_checked_at is None or
                 (now - self._regime_checked_at).seconds >= 1800)):
            self._refresh_regime("QQQ")
            self._regime_checked_at = now

        # ── Monitor stops/targets ──────────────────────────────────────────
        self._monitor_open_positions()

        # ── ORB entries 9:45 AM – noon ────────────────────────────────────
        if dtime(9, 45) <= now.time() <= dtime(12, 0):
            max_pos    = self.parameters["max_positions"]
            slots_used = len(self._positions)
            slots_free = max_pos - slots_used

            if slots_free > 0:
                # Collect all valid candidates across the watchlist
                candidates = []
                for symbol in self._load_symbols():
                    try:
                        c = self._process_symbol(symbol, now, today)
                        if c is not None:
                            candidates.append(c)
                    except Exception as e:
                        self.log_message(f"Error {symbol}: {e}")

                if candidates:
                    # Rank by conviction score descending
                    candidates.sort(key=lambda x: x["conviction"], reverse=True)

                    # Log full ranked list so you can see what was considered
                    ranked_log = ", ".join(
                        f"{c['symbol']}({c['conviction']:.0f})"
                        for c in candidates
                    )
                    self.log_message(
                        f"Candidates [{len(candidates)}] ranked: {ranked_log} | "
                        f"slots free: {slots_free}/{max_pos}"
                    )

                    # Execute top candidates up to available slots
                    executed = 0
                    for c in candidates:
                        if executed >= slots_free:
                            self.log_message(
                                f"SKIP {c['symbol']} (conviction:{c['conviction']:.0f}) "
                                f"— max_positions ({max_pos}) reached"
                            )
                            continue
                        # Re-check slot count in case a prior execution failed
                        if len(self._positions) >= max_pos:
                            break
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

                # ── Restore stop/target from trade journal if available ────
                # When the bot is restarted mid-position, the original ORB
                # stop and target are in the journal. Fall back to simple
                # percentage-based defaults only if no journal record exists.
                stop   = avg_price * 0.95
                target = avg_price * 1.10
                try:
                    row = self._journal.get_open_trade(ticker)
                    if row:
                        stop   = row.get("initial_stop",   stop)
                        target = row.get("initial_target", target)
                        self.log_message(
                            f"Restored {ticker}: stop={stop:.2f} target={target:.2f} "
                            f"(from journal)"
                        )
                    else:
                        self.log_message(
                            f"No journal record for {ticker} — using default "
                            f"stop={stop:.2f} target={target:.2f}"
                        )
                except Exception:
                    pass

                self._positions[ticker] = {
                    "symbol":       ticker,
                    "direction":    "LONG",
                    "entry_price":  avg_price,
                    "stop":         stop,
                    "target":       target,
                    "qty":          qty,
                    "entry_value":  self.portfolio_value,
                    "overnight_ok": overnight,
                    "synced":       True,
                }
                synced.append(ticker)
                if is_leveraged(ticker):
                    self.log_message(
                        f"⚠️  {ticker} is leveraged and open at market open "
                        f"— will close at EOD"
                    )
            if synced:
                self.log_message(f"Synced from Alpaca: {synced}")
        except Exception as e:
            self.log_message(f"Position sync failed: {e}")

    # ── Sell Signal Exit ──────────────────────────────────────────────────

    def _check_and_close_sell_signals(self, reason: str = "SELL_SIGNAL"):
        """
        Check every overnight-eligible open position against the current
        daily bias. If the symbol has flipped to SELL/STRONG_SELL, close
        the position immediately.

        In swing mode, applies a two-gate system:
          - Gate 1 (routine): days since last sell >= swing_sell_cooldown_days
          - Gate 2 (force): ALL THREE of conviction, bear_score, STRONG_SELL
            must fire simultaneously to override the cooldown early.

        Called in three places:
          1. During market hours (9:30–3:45): acts on prior-day bias
          2. After 3:50 PM preliminary signals: acts on fresh prelim prices
          3. After 4:15 PM final signals: acts on official close prices
        """
        swing_mode      = self.parameters.get("swing_mode", False)
        cooldown_days   = self.parameters.get("swing_sell_cooldown_days", 90)
        force_conv_gate = self.parameters.get("swing_force_sell_conviction", 85)
        force_bear_gate = self.parameters.get("swing_force_sell_bear_score", 5)

        for exec_ticker, pos in list(self._positions.items()):
            if not pos.get("overnight_ok", False):
                continue  # Leveraged positions handled by _close_leveraged_positions

            symbol = pos.get("symbol", exec_ticker)
            bias   = self._daily_bias.get(symbol, {})
            action = bias.get("action", "HOLD")

            if action not in ("SELL", "STRONG_SELL"):
                continue

            # ── Swing mode sell gate ──────────────────────────────────────
            if swing_mode:
                last_sell  = self._last_swing_sell.get(symbol)
                days_since = (ddate.today() - last_sell).days if last_sell else 999

                # Recompute conviction from bias cache
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

                # All THREE conditions must be true to override cooldown
                is_force_sell = (
                    conviction     >= force_conv_gate
                    and bear_score >= force_bear_gate
                    and action     == "STRONG_SELL"
                )

                if within_cooldown and not is_force_sell:
                    self.log_message(
                        f"[SWING] 🔒 Holding {symbol} — {days_since}d since last sell "
                        f"(cooldown={cooldown_days}d) | "
                        f"conviction={conviction:.0f} bear={bear_score} {action} — "
                        f"not strong enough to override"
                    )
                    continue  # Skip this sell

                if within_cooldown and is_force_sell:
                    self.log_message(
                        f"[SWING] ⚡ FORCE-SELL {symbol} — all three gates fired: "
                        f"conviction={conviction:.0f}>={force_conv_gate}, "
                        f"bear_score={bear_score}>={force_bear_gate}, "
                        f"action=STRONG_SELL | only {days_since}d since last sell — "
                        f"overriding {cooldown_days}d cooldown"
                    )
                else:
                    self.log_message(
                        f"[SWING] 📅 Routine sell {symbol} — {days_since}d >= "
                        f"{cooldown_days}d cooldown elapsed | "
                        f"conviction={conviction:.0f} bear={bear_score} {action}"
                    )

                # Record sell date before executing
                self._last_swing_sell[symbol] = ddate.today()

            else:
                # Normal day-trading path
                self.log_message(
                    f"SELL signal [{reason}] | closing {exec_ticker} "
                    f"(signal symbol: {symbol} → {action})"
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
                current   = float(bars.df["close"].iloc[-1])
                direction = pos["direction"]
                stop      = pos["stop"]
                target    = pos["target"]

                hit = None
                if direction == "LONG":
                    if current <= stop:     hit = "STOP"
                    elif current >= target: hit = "TARGET"
                elif direction == "SHORT":
                    if current >= stop:     hit = "STOP"
                    elif current <= target: hit = "TARGET"

                if hit:
                    self._close_single_position(exec_ticker, pos, hit, current)
            except Exception as e:
                self.log_message(f"Monitor error {exec_ticker}: {e}")

    def _close_single_position(self, exec_ticker: str, pos: dict,
                                reason: str, exit_price: float):
        try:
            position = self.get_position(exec_ticker)
            if position and int(position.quantity) > 0:
                self.submit_order(
                    self.create_order(exec_ticker, int(position.quantity), "sell")
                )
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

        o_tag = "overnight" if pos.get("overnight_ok") else "intraday"
        msg   = (
            f"CLOSED {exec_ticker} ({reason}) @ {exit_price:.2f} | "
            f"PnL: ${pnl:+.2f} | {o_tag}"
        )
        self.log_message(msg)
        self._notify(f"Trade-Bot: EXIT {exec_ticker}", msg)

        self._positions.pop(exec_ticker, None)
        self._trade_ids.pop(exec_ticker, None)
        symbol = pos.get("symbol")
        if symbol and symbol in self._orb_state:
            self._orb_state[symbol]["trade_taken"] = False

    # ── EOD: Close Leveraged Only ─────────────────────────────────────────

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

    # ── EOD Signal Runner ─────────────────────────────────────────────────

    def _run_eod_signals(self, label: str = "EOD"):
        """
        Run get_technical_signal() for all symbols and update the bias cache.

        label controls the log message:
          "PRELIM" — 3:50 PM run, prices ~10 min before close
          "FINAL"  — 4:15 PM run, official closing prices
          "PRE-MARKET" — fallback if no cache at startup

        BACKTEST MODE: When LUMIBOT_BACKTEST_MODE=true, routes to
        _run_eod_signals_backtest() which derives signals from simulated
        5-min bar data instead of calling the live Alpaca API.
        """
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            self._run_eod_signals_backtest(label)
            return

        api_key    = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_API_SECRET")
        symbols    = self._load_symbols()

        self.log_message(
            f"[{label}] Running signals for {len(symbols)} symbols..."
        )
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
        self.log_message(summary)
        self._notify(f"Trade-Bot: [{label}] EOD Signals", summary)

    def _run_eod_signals_backtest(self, label: str):
        """
        Backtest-mode bias generation using simulated 5-min bar data.

        Only processes symbols that are actually present in pandas_data
        (i.e. have data loaded into the backtesting engine). Avoids
        requesting "day" bars which LumiBot tries to synthesize from
        minutes and generates 400k-row lookback errors.

        Signal logic mirrors signal_engine.py: EMA 2/3/5 + RSI + MACD
        + SMA50/200 derived from the last 100 5-min bars of the day.
        """
        # Only run signals for symbols that have data in this backtest
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
            self.log_message(f"[{label}][BACKTEST] No symbols with data — skipping")
            return

        self.log_message(
            f"[{label}][BACKTEST] Deriving bias from 5-min bars for "
            f"{len(avail_symbols)}/{len(all_symbols)} symbols with data..."
        )

        new_bias = {}
        buys, sells = [], []

        for symbol in avail_symbols:
            try:
                # Use 5-min bars — the only timestep we have in pandas_data.
                # 100 bars = ~8 hours of data (enough for EMA/RSI on the day).
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

                ema2 = close.ewm(span=2,  adjust=False).mean()
                ema3 = close.ewm(span=3,  adjust=False).mean()
                ema5 = close.ewm(span=5,  adjust=False).mean()
                sma50  = close.rolling(min(50,  len(close))).mean()
                sma200 = close.rolling(min(200, len(close))).mean()

                delta = close.diff()
                gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
                loss  = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
                rsi   = float((100 - 100 / (1 + gain / loss.replace(0, 1e-10))).iloc[-1])

                ema12     = close.ewm(span=12, adjust=False).mean()
                ema26     = close.ewm(span=26, adjust=False).mean()
                macd_hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()

                price = float(close.iloc[-1])
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
                    "action":     action,
                    "bull_score": int(bull_score),
                    "bear_score": int(bear_score),
                    "rsi":        round(rsi, 2),
                    "vol_ratio":  round(vol_ratio, 2),
                    "date":       str(self.get_datetime().date()),
                    "source":     label,
                }

                if action in ("BUY", "STRONG_BUY"):
                    buys.append(symbol)
                elif action in ("SELL", "STRONG_SELL"):
                    sells.append(symbol)

            except Exception as e:
                self.log_message(f"[{label}][BACKTEST] Signal error {symbol}: {e}")
                new_bias[symbol] = {
                    "action": "HOLD", "bull_score": 0, "bear_score": 0,
                    "rsi": 50.0, "vol_ratio": 1.0,
                    "date": str(self.get_datetime().date()), "source": label,
                }

        self._daily_bias = new_bias
        self._save_bias(new_bias)
        self.log_message(
            f"[{label}][BACKTEST] Done | "
            f"BUY:{len(buys)} SELL:{len(sells)} "
            f"HOLD:{len(avail_symbols)-len(buys)-len(sells)}"
        )

    # ── Regime Refresh ────────────────────────────────────────────────────

    def _refresh_regime(self, symbol: str):
        try:
            bars_5m = self.get_historical_prices(symbol, 20, "5m")
            if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
                bars_15m = None
                bars_1h  = None
            else:
                bars_15m = self.get_historical_prices(symbol, 20, "15m")
                bars_1h  = self.get_historical_prices(symbol, 10, "1H")
            # Need at least 14 bars for ATR/RSI — skip silently at backtest start
            if bars_5m is None or len(bars_5m.df) < 14:
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
                .rolling(14).mean().iloc[-1]
            )
            regime = detect_regime(
                symbol=symbol,
                bars_5m=to_list(bars_5m),
                bars_15m=to_list(bars_15m),
                bars_1h=to_list(bars_1h),
                rsi_14=rsi_14, atr_14=atr_14,
            )
            self._journal.log_regime(symbol, regime)
            self.log_message(
                f"Regime [{symbol}]: {regime.get('regime')} "
                f"({regime.get('orb_suitability')}) "
                f"conf={regime.get('confidence',0):.2f}"
            )
        except Exception as e:
            self.log_message(f"Regime refresh failed: {e}")

    # ── Per-Symbol ORB Entry ──────────────────────────────────────────────

    def _process_symbol(self, symbol: str, now, today):
        bias   = self._daily_bias.get(symbol, {"action": "HOLD"})
        action = bias.get("action", "HOLD")

        hold_bias  = (action == "HOLD")
        want_long  = action in ("BUY", "STRONG_BUY")
        want_short = action in ("SELL", "STRONG_SELL")

        pair   = get_leveraged_pair(symbol)
        direct = is_direct_trade(symbol)

        # ── Bias gate ─────────────────────────────────────────────────────
        # BUY / STRONG_BUY  → allow LONG entry on ORB breakout  (full size)
        # HOLD              → allow LONG entry on ORB breakout  (half size via hold_override_size)
        # SELL / STRONG_SELL → block LONG entries; allow SHORT entry only
        if want_short:
            if direct:
                return None  # No inverse ETF for direct-trade symbols
        elif not want_long and not hold_bias:
            return None

        if want_short and direct:
            return None

        # ── Earnings filter ────────────────────────────────────────────────
        try:
            from strategies.earnings_filter import is_earnings_safe, get_earnings_info
            if not is_earnings_safe(symbol):
                info = get_earnings_info(symbol)
                self.log_message(
                    f"SKIP {symbol} — earnings in "
                    f"{info.get('hours_until','?')}h"
                )
                return None
        except Exception:
            pass  # If filter fails, allow trade through

        # ── ORB state ──────────────────────────────────────────────────────
        if symbol not in self._orb_state:
            self._orb_state[symbol] = {
                "or_high": None, "or_low": None, "or_mid": None,
                "or_established": False, "trade_taken": False,
            }
        state = self._orb_state[symbol]
        if state["trade_taken"]:
            return None

        # ── Fetch bars ─────────────────────────────────────────────────────
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

        # ── Establish OR ───────────────────────────────────────────────────
        if not state["or_established"]:
            w                = df_today.iloc[:3]
            state["or_high"] = float(w["high"].max())
            state["or_low"]  = float(w["low"].min())
            state["or_mid"]  = (state["or_high"] + state["or_low"]) / 2
            state["or_established"] = True

        current = float(df_today["close"].iloc[-1])

        # ── Regime check ───────────────────────────────────────────────────
        # In backtest mode, skip Ollama regime calls — assume moderate/neutral.
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
            regime          = get_cached_regime(symbol)
            if regime.get("regime") == "unknown":
                regime = get_cached_regime("QQQ")
            regime_type     = regime.get("regime", "unknown")
            regime_conf     = regime.get("confidence", 0.5)
            orb_suitability = regime.get("orb_suitability", "moderate")
            stop_adj        = regime.get("stop_adjustment", 1.0)
            target_adj      = regime.get("target_adjustment", 1.0)

            if regime_type == "low_liquidity" and regime_conf >= 0.70:
                self.log_message(f"SKIP {symbol} — low liquidity regime")
                return None

        # ── Check breakout ─────────────────────────────────────────────────
        is_long_break  = current > state["or_high"]
        is_short_break = current < state["or_low"]

        if want_long and not is_long_break:
            return None
        if want_short and not is_short_break:
            return None
        if hold_bias:
            if is_long_break:
                direction   = "LONG"
                exec_ticker = pair["bull"]
            elif is_short_break and not direct:
                direction   = "SHORT"
                exec_ticker = pair["bear"]
            else:
                return None

        if orb_suitability == "poor" and regime_conf >= 0.70:
            self.log_message(f"SKIP {symbol} — poor regime for ORB")
            return None

        # ── AI grading (skipped in backtest mode) ─────────────────────────
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            grading = {"approve": True, "confidence": 0.7, "size_multiplier": 1.0}
        else:
            candles = [{"o": r["open"], "h": r["high"],
                         "l": r["low"],  "c": r["close"], "v": r["volume"]}
                       for _, r in df_today.iterrows()]
            avg_vol = float(df_today["volume"].mean()) if not df_today.empty else 1.0
            grading = grade_setup(
                symbol=symbol, direction=direction if not hold_bias else "LONG",
                candles=candles,
                or_high=state["or_high"], or_low=state["or_low"],
                current_price=current, avg_volume=avg_vol,
            )

        ai_min = self.parameters.get("ai_min_confidence", 0.55)
        if grading["confidence"] < ai_min or not grading.get("approve", True):
            self.log_message(
                f"SKIP {symbol} — AI {grading['confidence']:.2f} | "
                f"{grading.get('reasoning','')[:80]}"
            )
            return None

        # ── Swing mode: resolve ticker and enforce entry rules ──────────────
        swing_mode = self.parameters.get("swing_mode", False)

        if swing_mode:
            from strategies.leverage_map import get_swing_ticker, is_leveraged_or_inverse

            # Swing mode is LONG-only — block all SHORT entries
            if want_short and not want_long:
                self.log_message(
                    f"[SWING] Skip SHORT {symbol} — inverse ETFs disabled in swing mode"
                )
                return None

            # Resolve to the unleveraged underlying ticker
            swing_ticker = get_swing_ticker(symbol)

            # Safety net: if somehow we still got a leveraged ticker, abort
            if is_leveraged_or_inverse(swing_ticker):
                self.log_message(
                    f"[SWING] ⚠️  Safety block — {swing_ticker} resolved as "
                    f"leveraged/inverse, refusing entry for {symbol}"
                )
                return None

            direction   = "LONG"
            exec_ticker = swing_ticker

            # Conviction preview gate — skip low-conviction setups before
            # wasting AI grading calls (grading already happened above, so
            # we use the grading confidence in the preview)
            swing_min_conv = self.parameters.get("swing_min_conviction", 75)
            preview_conviction = (
                grading.get("confidence", 0.60) * 40
                + bias.get("bull_score", 0) * 8
                + min(bias.get("vol_ratio", 1.0) - 1.0, 1.0) * 10
                + (10 if action in ("STRONG_BUY", "STRONG_SELL") else 0)
            )
            if preview_conviction < swing_min_conv:
                self.log_message(
                    f"[SWING] Skip {symbol} — conviction "
                    f"{preview_conviction:.0f} < {swing_min_conv} threshold"
                )
                return None

        else:
            # Normal day-trading mode — use leveraged pair
            if want_long or hold_bias:
                direction   = "LONG"
                exec_ticker = pair["bull"]
            else:
                direction   = "SHORT"
                exec_ticker = pair["bear"]

        if direction == "LONG" and pair["bear"] in self._positions:
            return None
        if direction == "SHORT" and pair["bull"] in self._positions:
            return None
        if exec_ticker in self._positions:
            return None

        # ── Dynamic sizing ─────────────────────────────────────────────────
        base_risk    = self.parameters["risk_pct"]
        size_mult    = grading.get("size_multiplier", 1.0)
        if hold_bias:
            size_mult *= self.parameters["hold_override_size"]
        if regime_type == "volatile":
            size_mult *= 0.75
        effective_risk = min(base_risk * size_mult, 0.02)

        risk_dist = abs(current - state["or_mid"]) * stop_adj
        if risk_dist <= 0:
            return None

        if direction == "LONG":
            initial_stop   = current - risk_dist
            initial_target = current + risk_dist * self.parameters["reward_ratio"] * target_adj
        else:
            initial_stop   = current + risk_dist
            initial_target = current - risk_dist * self.parameters["reward_ratio"] * target_adj

        qty = int((self.portfolio_value * effective_risk) / risk_dist)
        if qty < 1:
            return None

        # ── Conviction score for ranking ───────────────────────────────────
        # Higher = higher priority when max_positions would be exceeded.
        bull_score   = bias.get("bull_score", 0) if direction == "LONG" else bias.get("bear_score", 0)
        vol_ratio    = bias.get("vol_ratio", 1.0)
        action_bonus = 1 if action in ("STRONG_BUY", "STRONG_SELL") else 0
        conviction   = (
            grading["confidence"] * 40        # 0–40  (AI confidence weighted highest)
            + bull_score * 8                  # 0–40  (signal strength)
            + min(vol_ratio - 1.0, 1.0) * 10  # 0–10  (volume confirmation, capped at 2x)
            + action_bonus * 10               # 0–10  (STRONG signal bonus)
        )  # Base: 0–100

        # Pre-market boost: gap alignment, news sentiment, LLM sentiment signal
        # Only applies when direction aligns with the pre-market signal
        if _PREMARKET_AVAILABLE:
            try:
                pm_boost          = premarket_conviction_boost(bias)
                gap_signal        = bias.get("gap_signal", "FLAT")
                sentiment_signal  = bias.get("sentiment_signal", "HOLD")

                gap_aligned = (direction == "LONG" and gap_signal == "GAP_UP") or \
                              (direction == "SHORT" and gap_signal == "GAP_DOWN")
                sentiment_aligned = (direction == "LONG" and sentiment_signal == "LONG") or \
                                    (direction == "SHORT" and sentiment_signal == "SHORT")

                if gap_aligned or sentiment_aligned:
                    conviction += pm_boost
                elif gap_signal != "FLAT" and not gap_aligned:
                    conviction -= pm_boost * 0.5
            except Exception:
                pass  # Never let boost calculation block a trade

        # Return candidate — caller decides whether to execute based on ranking
        return {
            "symbol":          symbol,
            "exec_ticker":     exec_ticker,
            "direction":       direction,
            "current":         current,
            "qty":             qty,
            "initial_stop":    initial_stop,
            "initial_target":  initial_target,
            "effective_risk":  effective_risk,
            "size_mult":       size_mult,
            "hold_bias":       hold_bias,
            "conviction":      conviction,
            "grading":         grading,
            "regime":          regime,
            "regime_type":     regime_type,
            "orb_suitability": orb_suitability,
            "stop_adj":        stop_adj,
            "target_adj":      target_adj,
            "bias":            bias,
            "action":          action,
            "state":           state,
            "direct":          direct,
            "df_today":        df_today,
        }

    def _execute_candidate(self, c: dict):
        """Submit a pre-scored trade candidate returned by _process_symbol."""
        symbol      = c["symbol"]
        exec_ticker = c["exec_ticker"]
        direction   = c["direction"]
        current     = c["current"]
        qty         = c["qty"]
        grading     = c["grading"]
        regime      = c["regime"]
        bias        = c["bias"]
        action      = c["action"]
        hold_bias   = c["hold_bias"]
        state       = c["state"]
        direct      = c["direct"]

        try:
            order = self.create_order(exec_ticker, qty,
                                      "buy" if direction == "LONG" else "sell",
                                      time_in_force="day")
            self.submit_order(order)
        except Exception as e:
            self.log_message(f"Order failed {exec_ticker}: {e}")
            return

        overnight_eligible = direct and direction == "LONG"

        trade_id = self._journal.open_trade(
            symbol=symbol, exec_ticker=exec_ticker,
            direction=direction, entry_price=current, quantity=qty,
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
            "symbol":       symbol,
            "direction":    direction,
            "entry_price":  current,
            "stop":         c["initial_stop"],
            "target":       c["initial_target"],
            "qty":          qty,
            "entry_value":  self.portfolio_value,
            "overnight_ok": overnight_eligible,
        }
        self._trade_ids[exec_ticker] = trade_id
        state["trade_taken"] = True

        tag   = "HOLD-BIAS " if hold_bias else ""
        o_tag = " [OVERNIGHT]" if overnight_eligible else " [EOD]"
        swing_tag = " [SWING]" if self.parameters.get("swing_mode", False) else ""
        msg   = (
            f"{tag}ENTRY {direction}{swing_tag} | {symbol}→{exec_ticker} x{qty} "
            f"@ {current:.2f} | Stop:{c['initial_stop']:.2f} "
            f"Target:{c['initial_target']:.2f} | "
            f"AI:{grading['confidence']:.2f}({c['size_mult']:.1f}x) | "
            f"Conviction:{c['conviction']:.0f} | "
            f"Regime:{regime.get('regime','?')}{o_tag}"
        )
        self.log_message(msg)
        self._notify(f"Trade-Bot: {tag}ENTRY {direction} {exec_ticker}", msg)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _notify(self, subject: str, body: str):
        if os.getenv("LUMIBOT_BACKTEST_MODE", "").lower() == "true":
            return
        full = f"{subject}\n{body}"
        try:
            send_email(subject, body)
        except Exception:
            pass
        try:
            send_discord_message(full)
        except Exception:
            pass
        try:
            send_telegram_message(full)
        except Exception:
            pass

    def _load_symbols(self) -> list:
        try:
            with open(SYMBOLS_FILE, "r") as f:
                return [l.strip().upper() for l in f
                        if l.strip() and not l.startswith("#")]
        except FileNotFoundError:
            return ["QQQ"]

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

    # ── Shutdown ──────────────────────────────────────────────────────────

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