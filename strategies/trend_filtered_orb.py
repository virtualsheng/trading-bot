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
from strategies.earnings_filter import is_earnings_safe, get_earnings_info, clear_cache
from strategies.mean_reversion_strategy import (
    should_use_mean_reversion,
    get_mean_reversion_signal,
    compute_rsi,
    compute_atr,
)
from strategies.trade_journal import TradeJournal
from notifications.emailer import send_email
from notifications.discord import send_discord_message
from notifications.telegram import send_telegram_message


SYMBOLS_FILE  = "symbols.txt"
BIAS_CACHE    = "cache/daily_bias.json"
SIGNAL_HOUR   = 15
SIGNAL_MINUTE = 50

LEVERAGED_TICKERS = {
    "TQQQ","SQQQ","SPXL","SPXS","SOXL","SOXS","NVDL","NVDD",
    "UGL","GLL","AGQ","ZSL","JNUG","JDST","BITX","FAS","FAZ",
    "ERX","ERY","TSMU","PTIR","BITU","UCO","SCO","UPRO","SPXU",
}


def is_leveraged(ticker: str) -> bool:
    return ticker.upper() in LEVERAGED_TICKERS


class TrendFilteredORB(Strategy):

    parameters = {
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
        "mean_reversion_min_conf":  0.70,   # Min regime confidence to use MR strategy
    }

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def initialize(self):
        self.sleeptime = "5M"
        self.set_market("NYSE")

        self._starting_capital    = self.portfolio_value
        self._last_date           = None
        self._signals_run_today   = False
        self._market_opened_today = False
        self._regime_checked_at   = None

        self._orb_state  = {}   # symbol → ORB state
        self._positions  = {}   # exec_ticker → position info
        self._trade_ids  = {}   # exec_ticker → journal trade_id

        self._daily_bias = self._load_bias()
        self._journal    = TradeJournal()

        # ── Warm up Ollama immediately at startup ──────────────────────────
        # Model needs to be loaded into memory before the first trade fires.
        # Doing this now means zero delay when 9:45 AM arrives.
        try:
            from strategies.ai_engine import check_ollama_available
            available = check_ollama_available()
            if available:
                self.log_message("Ollama ready — AI grading active")
            else:
                self.log_message(
                    "⚠️  Ollama unavailable — trades will run at 0.5x size. "
                    "Start with: ollama serve"
                )
        except Exception as e:
            self.log_message(f"Ollama warmup error: {e}")

        self.log_message(
            f"Initialized | bias: {len(self._daily_bias)} symbols | "
            f"portfolio: ${self.portfolio_value:,.2f}"
        )

    def before_market_opens(self):
        # Clear earnings cache for new day
        try:
            from strategies.earnings_filter import clear_cache
            clear_cache()
        except Exception:
            pass

        # Pre-warm regime so first trade has a reading
        self._refresh_regime("QQQ")
        self._regime_checked_at = self.get_datetime()

        # ── Run signals if no bias cache ───────────────────────────────────
        if not self._daily_bias:
            self.log_message("No bias cache — running signals now")
            self._run_eod_signals()

    def on_trading_iteration(self):
        now   = self.get_datetime()
        today = now.date()

        # ── Daily reset ────────────────────────────────────────────────────
        if today != self._last_date:
            self._last_date           = today
            self._signals_run_today   = False
            self._market_opened_today = False
            self._orb_state           = {}

        # ── Sync positions from Alpaca at market open ──────────────────────
        if not self._market_opened_today and now.time() >= dtime(9, 30):
            self._sync_positions_from_broker()
            self._market_opened_today = True
            self.log_message(
                f"Market open | {len(self._positions)} positions carried"
            )

        # ── Close overnight positions with SELL signal ─────────────────────
        self._check_overnight_exits()

        # ── EOD signals at 3:50pm ──────────────────────────────────────────
        if now.time() >= dtime(SIGNAL_HOUR, SIGNAL_MINUTE) and not self._signals_run_today:
            self._run_eod_signals()
            self._signals_run_today = True

        # ── EOD exit leveraged only ────────────────────────────────────────
        eod_h, eod_m = map(int, self.parameters["eod_exit_time"].split(":"))
        if now.time() >= dtime(eod_h, eod_m):
            self._close_leveraged_positions("EOD")
            return

        if now.time() < dtime(9, 30):
            return

        # ── Regime refresh every 30 min ───────────────────────────────────
        if (self._regime_checked_at is None or
                (now - self._regime_checked_at).seconds >= 1800):
            self._refresh_regime("QQQ")
            self._regime_checked_at = now

        # ── Monitor stops/targets ──────────────────────────────────────────
        self._monitor_open_positions()

        # ── ORB / mean-reversion entries 9:45–noon ─────────────────────────
        if dtime(9, 45) <= now.time() <= dtime(12, 0):
            for symbol in self._load_symbols():
                try:
                    self._process_symbol(symbol, now, today)
                except Exception as e:
                    self.log_message(f"Error {symbol}: {e}")

    # ── Broker Sync ───────────────────────────────────────────────────────

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

    # ── Overnight Exits ───────────────────────────────────────────────────

    def _check_overnight_exits(self):
        for exec_ticker, pos in list(self._positions.items()):
            if not pos.get("overnight_ok", False):
                continue
            symbol = pos.get("symbol", exec_ticker)
            action = self._daily_bias.get(symbol, {}).get("action", "HOLD")
            if action in ("SELL", "STRONG_SELL"):
                self.log_message(
                    f"SELL signal — closing overnight {exec_ticker} ({symbol})"
                )
                try:
                    bars = self.get_historical_prices(exec_ticker, 2, "5m")
                    exit_price = (float(bars.df["close"].iloc[-1])
                                  if bars and len(bars.df) > 0
                                  else pos["entry_price"])
                except Exception:
                    exit_price = pos["entry_price"]
                self._close_single_position(exec_ticker, pos, "SELL_SIGNAL", exit_price)

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
                    if current <= stop:   hit = "STOP"
                    elif current >= target: hit = "TARGET"
                elif direction == "SHORT":
                    if current >= stop:   hit = "STOP"
                    elif current <= target: hit = "TARGET"

                if hit:
                    self._close_single_position(exec_ticker, pos, hit, current)
            except Exception as e:
                self.log_message(f"Monitor error {exec_ticker}: {e}")

    def _close_single_position(self, exec_ticker, pos, reason, exit_price):
        try:
            position = self.get_position(exec_ticker)
            if position and int(position.quantity) > 0:
                self.submit_order(
                    self.create_order(exec_ticker, int(position.quantity), "sell")
                )
        except Exception as e:
            self.log_message(f"Close failed {exec_ticker}: {e}")
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
        self.log_message(
            f"CLOSED {exec_ticker} ({reason}) @ {exit_price:.2f} | "
            f"PnL: ${pnl:+.2f} | {o_tag}"
        )
        self._notify(f"Trade-Bot: EXIT {exec_ticker}", 
                     f"{reason} @ {exit_price:.2f} | PnL ${pnl:+.2f}")

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
            overnight = list(self._positions.keys())
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

    def _run_eod_signals(self):
        api_key    = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_API_SECRET")
        symbols    = self._load_symbols()
        self.log_message(f"Running EOD signals for {len(symbols)} symbols...")

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
                }
                if action in ("BUY", "STRONG_BUY"):
                    buys.append(symbol)
                elif action in ("SELL", "STRONG_SELL"):
                    sells.append(symbol)
            except Exception as e:
                self.log_message(f"Signal error {symbol}: {e}")
                new_bias[symbol] = {"action": "HOLD",
                                    "date": str(datetime.now().date())}

        self._daily_bias = new_bias
        self._save_bias(new_bias)
        summary = (
            f"EOD Signals | BUY:{len(buys)} SELL:{len(sells)} "
            f"HOLD:{len(symbols)-len(buys)-len(sells)}\n"
            f"Buys:  {', '.join(buys[:12])}\n"
            f"Sells: {', '.join(sells[:12])}"
        )
        self.log_message(summary)
        self._notify("Trade-Bot: EOD Signals", summary)

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

    # ── Per-Symbol Entry Logic ────────────────────────────────────────────

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

        # ── EARNINGS FILTER ────────────────────────────────────────────────
        if self.parameters.get("earnings_filter_enabled", True):
            buffer_h = self.parameters.get("earnings_buffer_hours", 48)
            if not is_earnings_safe(symbol, buffer_hours=buffer_h):
                info = get_earnings_info(symbol)
                self.log_message(
                    f"SKIP {symbol} — earnings in "
                    f"{info.get('hours_until','?')}h "
                    f"({info.get('next_earnings','?')})"
                )
                return

        # ── REGIME CHECK ───────────────────────────────────────────────────
        regime = get_cached_regime(symbol)
        if regime.get("regime") == "unknown":
            regime = get_cached_regime("QQQ")  # Fall back to market-wide regime
        regime_type     = regime.get("regime", "unknown")
        regime_conf     = regime.get("confidence", 0.5)
        orb_suitability = regime.get("orb_suitability", "moderate")
        stop_adj        = regime.get("stop_adjustment", 1.0)
        target_adj      = regime.get("target_adjustment", 1.0)

        # Low liquidity — skip entirely regardless of signal
        if regime_type == "low_liquidity" and regime_conf >= 0.70:
            self.log_message(
                f"SKIP {symbol} — low liquidity regime "
                f"(conf={regime_conf:.2f})"
            )
            return

        # ── REGIME SWITCHING: Mean-reversion vs ORB ───────────────────────
        use_mr = (
            self.parameters.get("regime_switching_enabled", True)
            and should_use_mean_reversion(
                regime,
                min_confidence=self.parameters.get("mean_reversion_min_conf", 0.70)
            )
        )

        if use_mr:
            self._try_mean_reversion_entry(
                symbol=symbol,
                exec_ticker=exec_ticker,
                direction=direction,
                df_today=df_today,
                state=state,
                regime=regime,
                bias=bias,
                action=action,
                direct=direct,
                pair=pair,
                hold_bias=hold_bias,
            )
            return  # Don't also try ORB in the same iteration for this symbol

        # ── STANDARD ORB ENTRY ─────────────────────────────────────────────
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

        # Poor regime + low AI confidence = skip ORB
        if orb_suitability == "poor" and regime_conf >= 0.70:
            self.log_message(f"SKIP {symbol} — poor regime for ORB")
            return

        # AI grading
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

        # Dynamic sizing
        base_risk      = self.parameters["risk_pct"]
        size_mult      = grading.get("size_multiplier", 1.0)
        if hold_bias:
            size_mult *= self.parameters["hold_override_size"]
        # Volatile regime: reduce size
        if regime_type == "volatile":
            size_mult *= 0.75
            self.log_message(f"{symbol} volatile regime — reducing size to {size_mult:.2f}x")
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

        self._submit_and_record(
            symbol=symbol, exec_ticker=exec_ticker, direction=direction,
            current=current, qty=qty, initial_stop=initial_stop,
            initial_target=initial_target, effective_risk=effective_risk,
            grading=grading, regime=regime, bias=bias, action=action,
            state=state, direct=direct, hold_bias=hold_bias,
            entry_type="ORB",
        )

    # ── Mean-Reversion Entry ──────────────────────────────────────────────

    def _try_mean_reversion_entry(
        self, symbol, exec_ticker, direction, df_today, state,
        regime, bias, action, direct, pair, hold_bias,
    ):
        """
        Attempt a mean-reversion entry (fade the breakout).
        Only fires when regime is ranging/mean_reversion with high confidence.
        """
        if len(df_today) < 5:
            return

        rsi_current = compute_rsi(df_today["close"])
        atr         = compute_atr(df_today)

        mr_signal = get_mean_reversion_signal(
            df_today=df_today,
            or_high=state["or_high"],
            or_low=state["or_low"],
            or_mid=state["or_mid"],
            regime=regime,
            rsi_current=rsi_current,
            atr=atr,
        )

        if not mr_signal:
            return  # No valid mean-reversion setup

        # Mean-reversion direction may differ from bias direction
        mr_direction = mr_signal["direction"]

        # For MR SHORT: need a bear ETF — skip direct-trade symbols
        if mr_direction == "SHORT" and direct:
            self.log_message(
                f"SKIP {symbol} MR SHORT — no inverse ETF (direct trade)"
            )
            return

        mr_exec = pair["bull"] if mr_direction == "LONG" else pair["bear"]
        if mr_exec in self._positions:
            return

        # AI grading still applies — but with reduced confidence threshold for MR
        candles = [{"o": r["open"], "h": r["high"],
                     "l": r["low"],  "c": r["close"], "v": r["volume"]}
                   for _, r in df_today.iterrows()]
        avg_vol = float(df_today["volume"].mean()) if not df_today.empty else 1.0
        grading = grade_setup(
            symbol=symbol, direction=mr_direction, candles=candles,
            or_high=state["or_high"], or_low=state["or_low"],
            current_price=mr_signal["entry"], avg_volume=avg_vol,
        )

        # Mean-reversion uses slightly lower confidence bar (setup looks
        # different to the AI which is trained to approve momentum breakouts)
        if grading["confidence"] < 0.45:
            self.log_message(
                f"SKIP {symbol} MR — AI conf {grading['confidence']:.2f} too low"
            )
            return

        # Size at 0.75x for mean-reversion (more uncertain than momentum)
        effective_risk = min(self.parameters["risk_pct"] * 0.75, 0.015)
        risk_dist      = mr_signal["risk"]
        if risk_dist <= 0:
            return

        qty = int((self.portfolio_value * effective_risk) / risk_dist)
        if qty < 1:
            return

        self._submit_and_record(
            symbol=symbol, exec_ticker=mr_exec, direction=mr_direction,
            current=mr_signal["entry"], qty=qty,
            initial_stop=mr_signal["stop"], initial_target=mr_signal["target"],
            effective_risk=effective_risk, grading=grading, regime=regime,
            bias=bias, action=action, state=state, direct=direct,
            hold_bias=hold_bias, entry_type="MEAN_REVERSION",
            extra_log=mr_signal["reason"],
        )

    # ── Shared Order Submission + Journal ─────────────────────────────────

    def _submit_and_record(
        self, symbol, exec_ticker, direction, current, qty,
        initial_stop, initial_target, effective_risk, grading,
        regime, bias, action, state, direct, hold_bias,
        entry_type="ORB", extra_log="",
    ):
        try:
            order = self.create_order(exec_ticker, qty, "buy", time_in_force="day")
            self.submit_order(order)
        except Exception as e:
            self.log_message(f"Order failed {exec_ticker}: {e}")
            return

        overnight_ok = direct and direction == "LONG"
        size_mult    = grading.get("size_multiplier", 1.0)

        trade_id = self._journal.open_trade(
            symbol=symbol, exec_ticker=exec_ticker,
            direction=direction, entry_price=current, quantity=qty,
            or_high=state["or_high"], or_low=state["or_low"],
            or_mid=state["or_mid"], initial_stop=initial_stop,
            initial_target=initial_target, risk_pct=effective_risk,
            signal_action=action,
            signal_source=f"technical+{entry_type}",
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
            orb_suitability=regime.get("orb_suitability", "moderate"),
            stop_adjustment=regime.get("stop_adjustment", 1.0),
            target_adjustment=regime.get("target_adjustment", 1.0),
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
            "overnight_ok": overnight_ok,
            "entry_type":   entry_type,
        }
        self._trade_ids[exec_ticker] = trade_id
        state["trade_taken"] = True

        tag   = "HOLD-OVERRIDE " if hold_bias else ""
        o_tag = " [OVERNIGHT]" if overnight_ok else " [EOD]"
        msg   = (
            f"{tag}{entry_type} {direction} | {symbol}→{exec_ticker} x{qty} "
            f"@ {current:.2f} | Stop:{initial_stop:.2f} "
            f"Target:{initial_target:.2f} | "
            f"AI:{grading['confidence']:.2f}({size_mult:.1f}x) | "
            f"Regime:{regime.get('regime','?')}{o_tag}"
        )
        if extra_log:
            msg += f" | {extra_log}"
        self.log_message(msg)
        self._notify(f"Trade-Bot: {tag}{entry_type} {direction} {exec_ticker}", msg)

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