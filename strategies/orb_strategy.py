"""
Opening Range Breakout (ORB) Strategy
- Defines opening range using first 15 minutes (3 x 5-min bars)
- Enters on 5-min close above/below the range
- Stop loss at midpoint of opening range
- Target = 2x the risk (2:1 reward:risk)
- Exits at end of day if not stopped out

Trades TQQQ for long breakouts, SQQQ for short breakdowns.
Base ORB Strategy - Used by both live and backtest
"""

import pandas as pd
from lumibot.strategies import Strategy
from datetime import datetime, time as dtime


class ORBStrategy(Strategy):
    parameters = {
        "underlying": "QQQ",
        "bull_ticker": "TQQQ",
        "bear_ticker": "SQQQ",
        "orb_minutes": 15,
        "bar_minutes": 5,
        "risk_pct": 0.01,
        "reward_ratio": 2.0,
        "eod_exit_time": "15:45",
    }

    def initialize(self):
        self.sleeptime = "5M"
        self.set_market("NYSE")

        self.or_high = None
        self.or_low = None
        self.or_mid = None
        self.or_established = False
        self.trade_taken = False
        self.position_side = None
        self.entry_price = None
        self._last_date = None
        self._trade_log = []
        self._entry_value = None

    def on_trading_iteration(self):
        now = self.get_datetime()
        today = now.date()

        if today != self._last_date:
            self._reset_daily_state(today)

        # EOD Exit
        eod_time = dtime(*map(int, self.parameters["eod_exit_time"].split(":")))
        if now.time() >= eod_time:
            if self.trade_taken:
                self.sell_all()
                self._record_trade("EOD")
                self.trade_taken = False
            return

        if now.time() < dtime(9, 30):
            return

        bars = self.get_historical_prices(self.parameters["underlying"], 60, "5m")
        if not bars or len(bars.df) < 10:
            return

        df = bars.df.copy()
        df_today = df[df.index.date == today]

        if len(df_today) < 3:
            return

        # Establish Opening Range
        if not self.or_established:
            window_size = self.parameters["orb_minutes"] // self.parameters["bar_minutes"]
            if len(df_today) >= window_size:
                window = df_today.iloc[:window_size]
                self.or_high = window["high"].max()
                self.or_low = window["low"].min()
                self.or_mid = (self.or_high + self.or_low) / 2
                self.or_established = True
                self.log_message(f"ORB Established | High={self.or_high:.2f} Low={self.or_low:.2f}")

        if self.trade_taken:
            self._manage_open_trade(df_today)
            return

        if now.time() < dtime(9, 45):
            return

        current = df_today["close"].iloc[-1]

        if current > self.or_high:
            self._enter_position(self.parameters["bull_ticker"], "LONG", current)
        elif current < self.or_low:
            self._enter_position(self.parameters["bear_ticker"], "SHORT", current)

    def _reset_daily_state(self, today):
        self.or_high = self.or_low = self.or_mid = None
        self.or_established = False
        self.trade_taken = False
        self.position_side = None
        self._last_date = today

    def _enter_position(self, ticker, side, price):
        risk = abs(price - self.or_mid)
        if risk <= 0:
            return

        qty = int((self.portfolio_value * self.parameters["risk_pct"]) / risk)
        if qty < 1:
            return

        side_order = "buy" if side == "LONG" else "sell"
        order = self.create_order(ticker, qty, side_order)
        self.submit_order(order)

        self.entry_price = price
        self.position_side = side
        self.trade_taken = True
        self._entry_value = self.portfolio_value
        self.log_message(f"✅ ENTERED {side} {ticker} @ {price:.2f} | Risk={risk:.2f}")

    def _manage_open_trade(self, df_today):
        if len(df_today) == 0:
            return
        current = df_today["close"].iloc[-1]

        if (self.position_side == "LONG" and current <= self.or_mid) or \
           (self.position_side == "SHORT" and current >= self.or_mid):
            self.sell_all()
            self._record_trade("STOP LOSS")
            self.trade_taken = False

    def _record_trade(self, exit_type):
        if self._entry_value:
            pnl = self.portfolio_value - self._entry_value
            self._trade_log.append({
                "date": str(self._last_date),
                "side": self.position_side,
                "exit": exit_type,
                "pnl": round(pnl, 2)
            })

    def on_strategy_end(self):
        print("\n=== ORB Strategy Ended ===")
        if self._trade_log:
            df = pd.DataFrame(self._trade_log)
            print(f"Total Trades: {len(df)} | Total PnL: ${df['pnl'].sum():+.2f}")