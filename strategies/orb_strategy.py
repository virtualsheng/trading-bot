"""
Opening Range Breakout (ORB) Strategy — Cory Mitchell style
- Defines opening range using first 15 minutes (3 x 5-min bars)
- Enters on 5-min close above/below the range
- Stop loss at midpoint of opening range
- Target = 2x the risk (2:1 reward:risk)
- Exits at end of day if not stopped out

Trades TQQQ for long breakouts, SQQQ for short breakdowns.
"""

import numpy as np
import pandas as pd
from lumibot.strategies import Strategy
from datetime import datetime, time as dtime


class ORBStrategy(Strategy):

    parameters = {
        "underlying":    "QQQ",
        "bull_ticker":   "TQQQ",
        "bear_ticker":   "SQQQ",
        "orb_minutes":   15,    # Opening range window in minutes
        "bar_minutes":   5,     # Bar size in minutes
        "risk_pct":      0.01,  # Risk 1% of portfolio per trade
        "reward_ratio":  2.0,   # 2:1 reward:risk target
        "eod_exit_time": "15:45",
    }

    def initialize(self):
        self.sleeptime = "5M"
        self.set_market("NYSE")

        # Per-day state
        self.or_high        = None
        self.or_low         = None
        self.or_mid         = None
        self.or_established = False
        self.trade_taken    = False
        self.entry_price    = None
        self.stop_price     = None
        self.target_price   = None
        self.position_side  = None
        self._last_date     = None

        # Performance tracking
        self._trade_log     = []
        self._entry_value   = None

    def on_trading_iteration(self):
        now   = self.get_datetime()
        today = now.date()

        # ── Reset state at start of each new trading day ──────────────────
        if today != self._last_date:
            self.or_high        = None
            self.or_low         = None
            self.or_mid         = None
            self.or_established = False
            self.trade_taken    = False
            self.entry_price    = None
            self.stop_price     = None
            self.target_price   = None
            self.position_side  = None
            self._last_date     = today

        p    = self.parameters
        bull = p["bull_ticker"]
        bear = p["bear_ticker"]
        und  = p["underlying"]

        # ── EOD Exit ──────────────────────────────────────────────────────
        eod_h, eod_m = map(int, p["eod_exit_time"].split(":"))
        if now.time() >= dtime(eod_h, eod_m):
            if self.trade_taken:
                exit_value = self.portfolio_value
                pnl = exit_value - self._entry_value if self._entry_value else 0
                self._trade_log.append({
                    "date":   str(today),
                    "side":   self.position_side,
                    "exit":   "EOD",
                    "pnl":    round(pnl, 2),
                    "result": "WIN" if pnl > 0 else "LOSS"
                })
                self.sell_all()
                self.log_message(
                    f"EOD exit @ {now.time()} | "
                    f"PnL: ${pnl:+.2f}"
                )
                self.trade_taken   = False
                self.position_side = None
                self._entry_value  = None
            return

        # ── Only trade during market hours ────────────────────────────────
        if now.time() < dtime(9, 30):
            return

        # ── Get recent 5-min bars ─────────────────────────────────────────
        # Request enough bars to cover the OR window + buffer
        # orb_bars = 3 (for 15-min OR with 5-min bars)
        orb_bars  = p["orb_minutes"] // p["bar_minutes"]
        lookback  = orb_bars + 10  # extra buffer
        bars = self.get_historical_prices(und, lookback, "5m")

        if bars is None or len(bars.df) < orb_bars:
            return

        df = bars.df.copy()

        # ── KEY FIX: filter to TODAY's bars only ──────────────────────────
        # In backtesting, get_historical_prices returns a rolling window
        # that may span multiple days. We must isolate today's session
        # to correctly define the opening range.
        try:
            df_today = df[df.index.normalize() == pd.Timestamp(today, tz=df.index.tz)]
        except Exception:
            # Fallback if timezone handling varies
            df_today = df[df.index.date == today]

        if len(df_today) < orb_bars:
            return  # Today's session hasn't produced enough bars yet

        # ── Establish Opening Range from today's first N bars ─────────────
        if not self.or_established:
            or_window       = df_today.iloc[:orb_bars]
            self.or_high    = or_window["high"].max()
            self.or_low     = or_window["low"].min()
            self.or_mid     = (self.or_high + self.or_low) / 2
            self.or_established = True
            self.log_message(
                f"{today} OR: H={self.or_high:.2f} "
                f"L={self.or_low:.2f} Mid={self.or_mid:.2f}"
            )

        # ── Manage existing trade ─────────────────────────────────────────
        if self.trade_taken:
            self._manage_open_trade(df_today)
            return

        # ── Only enter AFTER the opening range is complete ────────────────
        # Don't trade during the first 15 minutes — wait for the OR to form
        if now.time() < dtime(9, 45):
            return

        # ── Check for breakout entry ───────────────────────────────────────
        current  = df_today["close"].iloc[-1]
        rr       = p["reward_ratio"]
        risk_pct = p["risk_pct"]

        if current > self.or_high:
            stop   = self.or_mid
            risk   = current - stop
            if risk <= 0:
                return
            target = current + (risk * rr)
            qty    = int((self.portfolio_value * risk_pct) / risk)

            if qty > 0:
                self.submit_order(self.create_order(bull, qty, "buy"))
                self.entry_price   = current
                self.stop_price    = stop
                self.target_price  = target
                self.position_side = "LONG"
                self.trade_taken   = True
                self._entry_value  = self.portfolio_value
                self.log_message(
                    f"ORB LONG {qty} {bull} @ {current:.2f} | "
                    f"OR High: {self.or_high:.2f} | "
                    f"Stop: {stop:.2f} | Target: {target:.2f}"
                )

        elif current < self.or_low:
            stop   = self.or_mid
            risk   = stop - current
            if risk <= 0:
                return
            target = current - (risk * rr)
            qty    = int((self.portfolio_value * risk_pct) / risk)

            if qty > 0:
                self.submit_order(self.create_order(bear, qty, "buy"))
                self.entry_price   = current
                self.stop_price    = stop
                self.target_price  = target
                self.position_side = "SHORT"
                self.trade_taken   = True
                self._entry_value  = self.portfolio_value
                self.log_message(
                    f"ORB SHORT {qty} {bear} @ {current:.2f} | "
                    f"OR Low: {self.or_low:.2f} | "
                    f"Stop: {stop:.2f} | Target: {target:.2f}"
                )

    def _manage_open_trade(self, df_today):
        """Check stop loss and profit target on open trade."""
        current = df_today["close"].iloc[-1]

        if self.position_side == "LONG":
            hit = None
            if current <= self.stop_price:
                hit = "STOP"
            elif current >= self.target_price:
                hit = "TARGET"
            if hit:
                exit_value = self.portfolio_value
                pnl = exit_value - self._entry_value if self._entry_value else 0
                self._trade_log.append({
                    "date":   str(self._last_date),
                    "side":   "LONG",
                    "exit":   hit,
                    "pnl":    round(pnl, 2),
                    "result": "WIN" if pnl > 0 else "LOSS"
                })
                self.sell_all()
                self.log_message(
                    f"{hit} (long) @ {current:.2f} | PnL: ${pnl:+.2f}"
                )
                self.trade_taken   = False
                self.position_side = None
                self._entry_value  = None

        elif self.position_side == "SHORT":
            hit = None
            if current >= self.stop_price:
                hit = "STOP"
            elif current <= self.target_price:
                hit = "TARGET"
            if hit:
                exit_value = self.portfolio_value
                pnl = exit_value - self._entry_value if self._entry_value else 0
                self._trade_log.append({
                    "date":   str(self._last_date),
                    "side":   "SHORT",
                    "exit":   hit,
                    "pnl":    round(pnl, 2),
                    "result": "WIN" if pnl > 0 else "LOSS"
                })
                self.sell_all()
                self.log_message(
                    f"{hit} (short) @ {current:.2f} | PnL: ${pnl:+.2f}"
                )
                self.trade_taken   = False
                self.position_side = None
                self._entry_value  = None

    def on_strategy_end(self):
        """
        Robust performance summary.
        Patches the pandas datetime bug in LumiBot's stats_summary
        before it crashes, then prints our own detailed tearsheet.
        """
        # ── Patch LumiBot's datetime bug ──────────────────────────────────
        try:
            if (hasattr(self, '_strategy_returns_df') and
                    self._strategy_returns_df is not None and
                    not self._strategy_returns_df.empty):
                idx = self._strategy_returns_df.index
                if hasattr(idx[0], 'timestamp'):
                    # Convert datetime objects to int64 nanoseconds
                    import pandas as pd
                    self._strategy_returns_df.index = pd.to_datetime(
                        [i.timestamp() * 1e9 for i in idx],
                        unit='ns', utc=True
                    )
        except Exception as patch_err:
            self.log_message(f"Stats patch note: {patch_err}")

        # ── Print our own tearsheet ────────────────────────────────────────
        sep  = "=" * 65
        sep2 = "-" * 65
        print(f"\n{sep}")
        print("  📊  ORB STRATEGY — BACKTEST PERFORMANCE SUMMARY")
        print(sep)

        try:
            initial_cash   = self.initial_cash
            final_value    = self.portfolio_value
            total_return   = (final_value / initial_cash - 1) * 100
            total_pnl      = final_value - initial_cash

            print(f"  Starting Capital : ${initial_cash:>12,.2f}")
            print(f"  Ending Value     : ${final_value:>12,.2f}")
            print(f"  Total P&L        : ${total_pnl:>+12,.2f}")
            print(f"  Total Return     : {total_return:>+11.2f}%")
        except Exception as e:
            print(f"  Portfolio summary unavailable: {e}")

        print(sep2)

        # Trade stats from our own log (most reliable)
        log = self._trade_log
        if log:
            wins       = [t for t in log if t["result"] == "WIN"]
            losses     = [t for t in log if t["result"] == "LOSS"]
            pnls       = [t["pnl"] for t in log]
            win_pnls   = [t["pnl"] for t in wins]
            loss_pnls  = [t["pnl"] for t in losses]
            longs      = [t for t in log if t["side"] == "LONG"]
            shorts     = [t for t in log if t["side"] == "SHORT"]
            stops      = [t for t in log if t["exit"] == "STOP"]
            targets    = [t for t in log if t["exit"] == "TARGET"]
            eod_exits  = [t for t in log if t["exit"] == "EOD"]

            win_rate   = len(wins) / len(log) * 100
            avg_win    = np.mean(win_pnls)  if win_pnls  else 0
            avg_loss   = np.mean(loss_pnls) if loss_pnls else 0
            profit_factor = (
                abs(sum(win_pnls)) / abs(sum(loss_pnls))
                if loss_pnls and sum(loss_pnls) != 0 else float('inf')
            )
            expectancy = np.mean(pnls) if pnls else 0

            # Sharpe from daily P&L
            if len(pnls) > 1:
                pnl_arr = np.array(pnls)
                sharpe  = (np.mean(pnl_arr) / np.std(pnl_arr)) * np.sqrt(252) if np.std(pnl_arr) > 0 else 0
            else:
                sharpe = 0

            # Max drawdown from cumulative PnL
            cum = np.cumsum(pnls)
            peak = np.maximum.accumulate(cum)
            drawdowns = cum - peak
            max_dd = drawdowns.min() if len(drawdowns) > 0 else 0

            print(f"  Total Trades     : {len(log)}")
            print(f"  Wins / Losses    : {len(wins)} / {len(losses)}")
            print(f"  Win Rate         : {win_rate:.1f}%")
            print(f"  Long / Short     : {len(longs)} / {len(shorts)}")
            print(sep2)
            print(f"  Avg Win          : ${avg_win:>+10.2f}")
            print(f"  Avg Loss         : ${avg_loss:>+10.2f}")
            print(f"  Profit Factor    : {profit_factor:.2f}x")
            print(f"  Expectancy/Trade : ${expectancy:>+10.2f}")
            print(sep2)
            print(f"  Sharpe Ratio     : {sharpe:.2f}")
            print(f"  Max Drawdown     : ${max_dd:>+10.2f}")
            print(sep2)
            print(f"  Exit Breakdown:")
            print(f"    Stop losses    : {len(stops)}")
            print(f"    Targets hit    : {len(targets)}")
            print(f"    EOD exits      : {len(eod_exits)}")
            print(sep2)

            # Best / worst trades
            if pnls:
                best_idx  = np.argmax(pnls)
                worst_idx = np.argmin(pnls)
                print(f"  Best Trade  : ${pnls[best_idx]:>+10.2f}  "
                      f"({log[best_idx]['date']} {log[best_idx]['side']})")
                print(f"  Worst Trade : ${pnls[worst_idx]:>+10.2f}  "
                      f"({log[worst_idx]['date']} {log[worst_idx]['side']})")

            # Quick verdict
            print(sep)
            if win_rate >= 50 and profit_factor >= 1.5 and total_return > 0:
                verdict = "✅ PROFITABLE — solid edge detected"
            elif win_rate >= 45 and profit_factor >= 1.2:
                verdict = "🟡 MARGINAL — needs tuning or better entries"
            else:
                verdict = "🔴 UNPROFITABLE — review parameters"
            print(f"  Verdict: {verdict}")

        else:
            # Fall back to LumiBot's trade list if our log is empty
            try:
                lumibot_trades = self.get_trades()
                if lumibot_trades:
                    wins = [t for t in lumibot_trades if getattr(t, 'pnl', 0) > 0]
                    print(f"  Total Trades : {len(lumibot_trades)}")
                    print(f"  Win Rate     : {len(wins)/len(lumibot_trades)*100:.1f}%")
                else:
                    print("  ⚠️  No trades executed during backtest period.")
                    print("  Check: is QQQ data covering regular market hours (9:30-16:00)?")
                    print("  Check: is the OR forming before the 9:45 entry window?")
            except Exception as e:
                print(f"  Trade data unavailable: {e}")

        print(sep + "\n")

    def on_abrupt_closing(self):
        self.sell_all()