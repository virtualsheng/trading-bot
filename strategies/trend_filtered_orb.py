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

Key behaviors from v3/v4 unchanged:
  - Leveraged/inverse ETFs closed at 3:45 PM
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
from datetime import datetime, time as dtime
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


SYMBOLS_FILE  = "symbols.txt"
BIAS_CACHE    = "cache/daily_bias.json"

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

        self._daily_bias = self._load_bias()
        self._journal    = TradeJournal()

        # ── Warm up Ollama immediately at script start ─────────────────────
        # Model loads into memory now so it's ready when first trade fires.
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

        self.log_message(
            f"Initialized | bias: {len(self._daily_bias)} symbols | "
            f"portfolio: ${self.portfolio_value:,.2f}"
        )

    def before_market_opens(self):
        """Called once before each market session (~9:00–9:15 AM ET)."""
        # Clear earnings cache for new day
        try:
            from strategies.earnings_filter import clear_cache
            clear_cache()
        except Exception:
            pass

        # Pre-warm regime so first trade has a reading immediately
        self._refresh_regime("QQQ")
        self._regime_checked_at = self.get_datetime()

        # Run signals if no bias cache from yesterday
        if not self._daily_bias:
            self.log_message("No bias cache — running preliminary signals now")
            self._run_eod_signals(label="PRE-MARKET")

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

        # ── Regime refresh every 30 min ───────────────────────────────────
        if (self._regime_checked_at is None or
                (now - self._regime_checked_at).seconds >= 1800):
            self._refresh_regime("QQQ")
            self._regime_checked_at = now

        # ── Monitor stops/targets ──────────────────────────────────────────
        self._monitor_open_positions()

        # ── ORB entries 9:45 AM – noon ────────────────────────────────────
        if dtime(9, 45) <= now.time() <= dtime(12, 0):
            for symbol in self._load_symbols():
                try:
                    self._process_symbol(symbol, now, today)
                except Exception as e:
                    self.log_message(f"Error {symbol}: {e}")

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
                self._positions[ticker] = {
                    "symbol":       ticker,
                    "direction":    "LONG",
                    "entry_price":  avg_price,
                    "stop":         avg_price * 0.95,
                    "target":       avg_price * 1.10,
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

        Called in three places:
          1. During market hours (9:30–3:45): acts on prior-day bias
          2. After 3:50 PM preliminary signals: acts on fresh prelim prices
          3. After 4:15 PM final signals: acts on official close prices

        This replaces the old _check_overnight_exits() which only ran once
        per iteration and never re-ran after signals were refreshed.
        """
        for exec_ticker, pos in list(self._positions.items()):
            if not pos.get("overnight_ok", False):
                continue  # Leveraged positions handled by _close_leveraged_positions

            symbol = pos.get("symbol", exec_ticker)
            action = self._daily_bias.get(symbol, {}).get("action", "HOLD")

            if action not in ("SELL", "STRONG_SELL"):
                continue

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
        """
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

    # ── Regime Refresh ────────────────────────────────────────────────────

    def _refresh_regime(self, symbol: str):
        try:
            bars_5m  = self.get_historical_prices(symbol, 20, "5m")
            bars_15m = self.get_historical_prices(symbol, 20, "15m")
            bars_1h  = self.get_historical_prices(symbol, 10, "1H")
            if bars_5m is None:
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

        if hold_bias and not self.parameters.get("hold_override", False):
            return

        pair   = get_leveraged_pair(symbol)
        direct = is_direct_trade(symbol)

        if want_short and direct:
            return
        if len(self._positions) >= self.parameters["max_positions"]:
            return

        if want_long or hold_bias:
            direction   = "LONG"
            exec_ticker = pair["bull"]
        else:
            direction   = "SHORT"
            exec_ticker = pair["bear"]

        if direction == "LONG" and pair["bear"] in self._positions:
            return
        if direction == "SHORT" and pair["bull"] in self._positions:
            return
        if exec_ticker in self._positions:
            return

        # ── Earnings filter ────────────────────────────────────────────────
        try:
            from strategies.earnings_filter import is_earnings_safe, get_earnings_info
            if not is_earnings_safe(symbol):
                info = get_earnings_info(symbol)
                self.log_message(
                    f"SKIP {symbol} — earnings in "
                    f"{info.get('hours_until','?')}h"
                )
                return
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
            return

        # ── Fetch bars ─────────────────────────────────────────────────────
        bars = self.get_historical_prices(symbol, 20, "5m")
        if bars is None or len(bars.df) < 3:
            return
        df = bars.df.copy()

        try:
            tz       = df.index.tz
            df_today = df[df.index.normalize() == pd.Timestamp(today, tz=tz)]
        except Exception:
            df_today = df[pd.to_datetime(df.index.date) == pd.Timestamp(today)]

        if len(df_today) < 3:
            return

        # ── Establish OR ───────────────────────────────────────────────────
        if not state["or_established"]:
            w                = df_today.iloc[:3]
            state["or_high"] = float(w["high"].max())
            state["or_low"]  = float(w["low"].min())
            state["or_mid"]  = (state["or_high"] + state["or_low"]) / 2
            state["or_established"] = True

        current = float(df_today["close"].iloc[-1])

        # ── Regime check ───────────────────────────────────────────────────
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
            return

        # ── Check breakout ─────────────────────────────────────────────────
        is_long_break  = current > state["or_high"]
        is_short_break = current < state["or_low"]

        if want_long and not is_long_break:
            return
        if want_short and not is_short_break:
            return
        if hold_bias:
            if is_long_break:
                direction   = "LONG"
                exec_ticker = pair["bull"]
            elif is_short_break and not direct:
                direction   = "SHORT"
                exec_ticker = pair["bear"]
            else:
                return

        if orb_suitability == "poor" and regime_conf >= 0.70:
            self.log_message(f"SKIP {symbol} — poor regime for ORB")
            return

        # ── AI grading ─────────────────────────────────────────────────────
        candles = [{"o": r["open"], "h": r["high"],
                     "l": r["low"],  "c": r["close"], "v": r["volume"]}
                   for _, r in df_today.iterrows()]
        avg_vol = float(df_today["volume"].mean()) if not df_today.empty else 1.0
        grading = grade_setup(
            symbol=symbol, direction=direction, candles=candles,
            or_high=state["or_high"], or_low=state["or_low"],
            current_price=current, avg_volume=avg_vol,
        )

        if not grading.get("approve", True):
            self.log_message(
                f"SKIP {symbol} — AI {grading['confidence']:.2f} | "
                f"{grading.get('reasoning','')[:80]}"
            )
            return

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
            return

        if direction == "LONG":
            initial_stop   = current - risk_dist
            initial_target = current + risk_dist * self.parameters["reward_ratio"] * target_adj
        else:
            initial_stop   = current + risk_dist
            initial_target = current - risk_dist * self.parameters["reward_ratio"] * target_adj

        qty = int((self.portfolio_value * effective_risk) / risk_dist)
        if qty < 1:
            return

        # ── Submit ─────────────────────────────────────────────────────────
        try:
            order = self.create_order(exec_ticker, qty, "buy",
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
            or_mid=state["or_mid"], initial_stop=initial_stop,
            initial_target=initial_target, risk_pct=effective_risk,
            signal_action=action, signal_source="technical+ORB",
            bull_score=bias.get("bull_score", 0),
            bear_score=bias.get("bear_score", 0),
            signal_rsi=bias.get("rsi", 50),
            signal_vol_ratio=bias.get("vol_ratio", 1.0),
            ai_confidence=grading["confidence"],
            ai_size_mult=size_mult,
            ai_flags=grading.get("flags", []),
            ai_vol_quality=grading.get("volume_quality", "unknown"),
            ai_pa_quality=grading.get("price_action_quality", "unknown"),
            ai_approved=grading.get("approve", True),
            regime=regime.get("regime", "unknown"),
            regime_conf=regime.get("confidence", 0.5),
            orb_suitability=orb_suitability,
            stop_adjustment=stop_adj, target_adjustment=target_adj,
            portfolio_value=self.portfolio_value,
            open_positions=len(self._positions),
        )

        self._positions[exec_ticker] = {
            "symbol":       symbol,
            "direction":    direction,
            "entry_price":  current,
            "stop":         initial_stop,
            "target":       initial_target,
            "qty":          qty,
            "entry_value":  self.portfolio_value,
            "overnight_ok": overnight_eligible,
        }
        self._trade_ids[exec_ticker] = trade_id
        state["trade_taken"] = True

        tag   = "HOLD-OVERRIDE " if hold_bias else ""
        o_tag = " [OVERNIGHT]" if overnight_eligible else " [EOD]"
        msg   = (
            f"{tag}ENTRY {direction} | {symbol}→{exec_ticker} x{qty} "
            f"@ {current:.2f} | Stop:{initial_stop:.2f} "
            f"Target:{initial_target:.2f} | "
            f"AI:{grading['confidence']:.2f}({size_mult:.1f}x) | "
            f"Regime:{regime.get('regime','?')}{o_tag}"
        )
        self.log_message(msg)
        self._notify(f"Trade-Bot: {tag}ENTRY {direction} {exec_ticker}", msg)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _notify(self, subject: str, body: str):
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

    def _load_bias(self) -> dict:
        try:
            os.makedirs("cache", exist_ok=True)
            if os.path.exists(BIAS_CACHE):
                with open(BIAS_CACHE) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_bias(self, bias: dict):
        try:
            os.makedirs("cache", exist_ok=True)
            with open(BIAS_CACHE, "w") as f:
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