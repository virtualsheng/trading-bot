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
"""

import os
import json
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime
from lumibot.strategies import Strategy

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data import get_price_data
from strategies.signal_engine import get_technical_signal
from strategies.leverage_map import get_leveraged_pair
from strategies.ai_engine import grade_setup, detect_regime, get_cached_regime, narrate_trade
from strategies.trade_journal import TradeJournal
from notifications.emailer import send_email
from notifications.discord import send_discord_message
from notifications.telegram import send_telegram_message


SYMBOLS_FILE  = "symbols.txt"
BIAS_CACHE    = "cache/daily_bias.json"
MAX_POSITIONS = 3
SIGNAL_HOUR   = 15
SIGNAL_MINUTE = 50
BASE_RISK_PCT  = 0.01   # 1% base risk per trade


class TrendFilteredORB(Strategy):

    parameters = {
        "orb_minutes":   15,
        "bar_minutes":   5,
        "risk_pct":      BASE_RISK_PCT,
        "reward_ratio":  2.0,
        "eod_exit_time": "15:45",
        "max_positions": MAX_POSITIONS,
        "ai_min_confidence": 0.55,   # Skip trades below this
        "hold_override_size": 0.5,   # Size multiplier when bias=HOLD
    }

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def initialize(self):
        self.sleeptime = "5M"
        self.set_market("NYSE")

        self._starting_capital   = self.portfolio_value
        self._last_date          = None
        self._signals_run_today  = False
        self._regime_checked_at  = None

        self._orb_state  = {}   # symbol → ORB state dict
        self._positions  = {}   # exec_ticker → position info dict
        self._trade_ids  = {}   # exec_ticker → journal trade_id

        self._daily_bias = self._load_bias()
        self._journal    = TradeJournal()

        self.log_message(
            f"Initialized | bias for {len(self._daily_bias)} symbols | "
            f"portfolio: ${self.portfolio_value:,.2f}"
        )

    def before_market_opens(self):
        """Refresh bias if none loaded and market hasn't opened yet."""
        if not self._daily_bias:
            self.log_message("No bias cache found — running EOD signals now")
            self._run_eod_signals()

    def on_trading_iteration(self):
        now   = self.get_datetime()
        today = now.date()

        # ── Daily reset ────────────────────────────────────────────────────
        if today != self._last_date:
            self._last_date         = today
            self._signals_run_today = False
            self._orb_state         = {}
            self.log_message(f"New day: {today} | Bias symbols: {len(self._daily_bias)}")

        # ── EOD signal run at 3:50pm ───────────────────────────────────────
        if (now.time() >= dtime(SIGNAL_HOUR, SIGNAL_MINUTE) and
                not self._signals_run_today):
            self._run_eod_signals()
            self._signals_run_today = True

        # ── EOD position exit at 3:45pm ────────────────────────────────────
        eod_h, eod_m = map(int, self.parameters["eod_exit_time"].split(":"))
        if now.time() >= dtime(eod_h, eod_m):
            self._close_all_positions("EOD")
            return

        if now.time() < dtime(9, 30):
            return

        # ── Refresh regime every 30 minutes ───────────────────────────────
        if (self._regime_checked_at is None or
                (now - self._regime_checked_at).seconds >= 1800):
            self._refresh_regime("QQQ", today)
            self._regime_checked_at = now

        # ── Process each symbol ────────────────────────────────────────────
        for symbol in self._load_symbols():
            try:
                self._process_symbol(symbol, now, today)
            except Exception as e:
                self.log_message(f"Error processing {symbol}: {e}")

    # ── EOD Signal Runner ─────────────────────────────────────────────────

    def _run_eod_signals(self):
        api_key    = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_API_SECRET")
        symbols    = self._load_symbols()

        self.log_message(f"Running EOD signals for {len(symbols)} symbols...")
        new_bias  = {}
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
                new_bias[symbol] = {"action": "HOLD", "date": str(datetime.now().date())}

        self._daily_bias = new_bias
        self._save_bias(new_bias)

        summary = (
            f"EOD Signals complete | "
            f"BUY:{len(buys)} SELL:{len(sells)} "
            f"HOLD:{len(symbols)-len(buys)-len(sells)}\n"
            f"Buys:  {', '.join(buys[:12])}\n"
            f"Sells: {', '.join(sells[:12])}"
        )
        self.log_message(summary)
        self._notify("EOD Signal Update", summary)

    # ── Regime Refresh ────────────────────────────────────────────────────

    def _refresh_regime(self, symbol: str, today):
        """Fetch multi-timeframe bars and run regime detection."""
        try:
            api_key    = os.getenv("ALPACA_API_KEY")
            secret_key = os.getenv("ALPACA_API_SECRET")

            df_5m  = get_price_data(symbol, api_key, secret_key, days=3,
                                    timeframe_minutes=5)
            df_15m = get_price_data(symbol, api_key, secret_key, days=5,
                                    timeframe_minutes=15)
            df_1h  = get_price_data(symbol, api_key, secret_key, days=10,
                                    timeframe_minutes=60)

            def to_list(df):
                return [
                    {"o": r["open"], "h": r["high"],
                     "l": r["low"],  "c": r["close"], "v": r["volume"]}
                    for _, r in df.tail(20).iterrows()
                ]

            close   = df_5m["close"]
            rsi_14  = float(close.ewm(span=14).mean().iloc[-1])
            atr_14  = float((df_5m["high"] - df_5m["low"]).rolling(14).mean().iloc[-1])

            regime = detect_regime(
                symbol=symbol,
                bars_5m=to_list(df_5m),
                bars_15m=to_list(df_15m),
                bars_1h=to_list(df_1h),
                rsi_14=rsi_14,
                atr_14=atr_14,
            )
            self._journal.log_regime(symbol, regime)
            self.log_message(
                f"Regime [{symbol}]: {regime.get('regime')} "
                f"({regime.get('orb_suitability')}) "
                f"conf={regime.get('confidence', 0):.2f}"
            )
        except Exception as e:
            self.log_message(f"Regime refresh failed: {e}")

    # ── Per-Symbol ORB Processing ─────────────────────────────────────────

    def _process_symbol(self, symbol: str, now, today):
        bias   = self._daily_bias.get(symbol, {"action": "HOLD"})
        action = bias.get("action", "HOLD")

        pair     = get_leveraged_pair(symbol)
        want_long = action in ("BUY", "STRONG_BUY")
        want_short = action in ("SELL", "STRONG_SELL")
        hold_bias  = action == "HOLD"

        # Cap positions
        if len(self._positions) >= self.parameters["max_positions"]:
            return

        exec_ticker = pair["bull"] if (want_long or hold_bias) else pair["bear"]
        if exec_ticker in self._positions:
            return

        # ORB state
        if symbol not in self._orb_state:
            self._orb_state[symbol] = {
                "or_high": None, "or_low": None, "or_mid": None,
                "or_established": False, "trade_taken": False,
            }
        state = self._orb_state[symbol]
        if state["trade_taken"]:
            return

        # Fetch today's 5-min bars via LumiBot's built-in data
        bars = self.get_historical_prices(symbol, 20, "5m")
        if bars is None or len(bars.df) < 3:
            return
        df = bars.df.copy()

        # Filter to today
        try:
            tz = df.index.tz
            df_today = df[df.index.normalize() == pd.Timestamp(today, tz=tz)]
        except Exception:
            df_today = df[pd.to_datetime(df.index.date) == pd.Timestamp(today)]
        if len(df_today) < 3:
            return

        # Establish OR from first 3 bars (9:30-9:45)
        if not state["or_established"]:
            w = df_today.iloc[:3]
            state["or_high"] = float(w["high"].max())
            state["or_low"]  = float(w["low"].min())
            state["or_mid"]  = (state["or_high"] + state["or_low"]) / 2
            state["or_established"] = True

        if now.time() < dtime(9, 45):
            return

        current = float(df_today["close"].iloc[-1])
        is_long_breakout  = current > state["or_high"]
        is_short_breakout = current < state["or_low"]

        # Determine trade direction
        if want_long and is_long_breakout:
            direction   = "LONG"
            exec_ticker = pair["bull"]
        elif want_short and is_short_breakout:
            direction   = "SHORT"
            exec_ticker = pair["bear"]
        elif hold_bias and is_long_breakout:
            direction   = "LONG"
            exec_ticker = pair["bull"]
        elif hold_bias and is_short_breakout:
            direction   = "SHORT"
            exec_ticker = pair["bear"]
        else:
            return  # No breakout or direction mismatch

        # ── AI Setup Grading ───────────────────────────────────────────────
        candles = [
            {"o": r["open"], "h": r["high"],
             "l": r["low"],  "c": r["close"], "v": r["volume"]}
            for _, r in df_today.iterrows()
        ]
        avg_vol = float(df_today["volume"].mean()) if not df_today.empty else 1
        grading = grade_setup(
            symbol=symbol,
            direction=direction,
            candles=candles,
            or_high=state["or_high"],
            or_low=state["or_low"],
            current_price=current,
            avg_volume=avg_vol,
        )

        if not grading.get("approve", True):
            self.log_message(
                f"SKIP {symbol} — AI confidence {grading['confidence']:.2f} "
                f"< threshold | {grading.get('reasoning', '')[:80]}"
            )
            return

        # ── Dynamic Position Sizing ────────────────────────────────────────
        base_risk    = self.parameters["risk_pct"]
        size_mult    = grading.get("size_multiplier", 1.0)
        if hold_bias:
            size_mult *= self.parameters["hold_override_size"]  # Half size for HOLD override
        effective_risk = min(base_risk * size_mult, 0.02)  # Cap at 2%

        # ── Regime-adjusted stop/target ────────────────────────────────────
        regime            = get_cached_regime(symbol)
        stop_adj          = regime.get("stop_adjustment", 1.0)
        target_adj        = regime.get("target_adjustment", 1.0)
        orb_suitability   = regime.get("orb_suitability", "moderate")

        # Skip if regime is poor and confidence not elite
        if orb_suitability == "poor" and grading["confidence"] < 0.80:
            self.log_message(
                f"SKIP {symbol} — poor regime ({regime.get('regime')}) "
                f"with low confidence {grading['confidence']:.2f}"
            )
            return

        risk_dist   = abs(current - state["or_mid"]) * stop_adj
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

        # ── Submit order ───────────────────────────────────────────────────
        try:
            order = self.create_order(exec_ticker, qty, "buy", time_in_force="day")
            self.submit_order(order)
        except Exception as e:
            self.log_message(f"Order failed {exec_ticker}: {e}")
            return

        # ── Record in journal ──────────────────────────────────────────────
        trade_id = self._journal.open_trade(
            symbol=symbol,
            exec_ticker=exec_ticker,
            direction=direction,
            entry_price=current,
            quantity=qty,
            or_high=state["or_high"],
            or_low=state["or_low"],
            or_mid=state["or_mid"],
            initial_stop=initial_stop,
            initial_target=initial_target,
            risk_pct=effective_risk,
            signal_action=action,
            signal_source="technical+ORB",
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
            stop_adjustment=stop_adj,
            target_adjustment=target_adj,
            portfolio_value=self.portfolio_value,
            open_positions=len(self._positions),
        )

        # Track position
        self._positions[exec_ticker] = {
            "symbol":       symbol,
            "direction":    direction,
            "entry_price":  current,
            "stop":         initial_stop,
            "target":       initial_target,
            "qty":          qty,
            "entry_value":  self.portfolio_value,
        }
        self._trade_ids[exec_ticker] = trade_id
        state["trade_taken"] = True

        msg = (
            f"{'HOLD-OVERRIDE ' if hold_bias else ''}"
            f"ENTRY {direction} | {symbol}→{exec_ticker} "
            f"x{qty} @ {current:.2f} | "
            f"Stop:{initial_stop:.2f} Target:{initial_target:.2f} | "
            f"AI:{grading['confidence']:.2f}({size_mult:.1f}x) | "
            f"Regime:{regime.get('regime','?')} | "
            f"{pair.get('note','')}"
        )
        self.log_message(msg)
        self._notify(f"TRADE: {direction} {exec_ticker}", msg)

    # ── Position Close ────────────────────────────────────────────────────

    def _close_all_positions(self, reason: str):
        if not self._positions:
            return
        self.sell_all()
        for exec_ticker, pos in list(self._positions.items()):
            trade_id = self._trade_ids.get(exec_ticker)
            pnl      = self.portfolio_value - pos["entry_value"]

            # Build trade record for narrative
            record = {**pos, "exit_reason": reason,
                      "pnl": round(pnl, 2),
                      "exec_ticker": exec_ticker}

            narrative = narrate_trade(record)

            if trade_id:
                self._journal.close_trade(
                    trade_id=trade_id,
                    exit_price=pos["entry_price"],  # Approximate — no fill price on batch close
                    exit_reason=reason,
                    portfolio_value=self.portfolio_value,
                    ai_narrative=narrative,
                )

            self.log_message(
                f"CLOSED {exec_ticker} ({reason}) | "
                f"PnL: ${pnl:+.2f} | {narrative[:80]}"
            )

        self._positions.clear()
        self._trade_ids.clear()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _load_symbols(self) -> list:
        try:
            with open(SYMBOLS_FILE, "r") as f:
                return [
                    line.strip().upper()
                    for line in f
                    if line.strip() and not line.startswith("#")
                ]
        except FileNotFoundError:
            return ["QQQ"]

    def _load_bias(self) -> dict:
        try:
            os.makedirs("cache", exist_ok=True)
            if os.path.exists(BIAS_CACHE):
                with open(BIAS_CACHE, "r") as f:
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

    def _notify(self, subject: str, body: str):
        for fn, name in [
            (send_email,            "email"),
            (send_discord_message,  "discord"),
            (send_telegram_message, "telegram"),
        ]:
            try:
                fn(subject, body) if name == "email" else fn(body)
            except Exception:
                pass

    # ── Shutdown ──────────────────────────────────────────────────────────

    def on_strategy_end(self):
        self._close_all_positions("STRATEGY_END")
        stats = self._journal.get_stats(days=30)
        self.log_message(
            f"30-day stats: {stats.get('total_trades', 0)} trades | "
            f"Win rate: {stats.get('win_rate', 0)}% | "
            f"PnL: ${stats.get('total_pnl', 0):+.2f} | "
            f"PF: {stats.get('profit_factor', 0):.2f}"
        )
        self._journal.export_csv()

    def on_abrupt_closing(self):
        self.sell_all()