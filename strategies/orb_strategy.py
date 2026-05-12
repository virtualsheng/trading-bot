"""
Base ORB Strategy - Used by both live and backtest
Opening Range Breakout (ORB) Strategy
- Defines opening range using first 15 minutes (3 x 5-min bars)
- Enters on 5-min close above/below the range
- Stop loss at midpoint of opening range
- Target = 2x the risk (2:1 reward:risk)
- Exits at end of day if not stopped out

Trades TQQQ for long breakouts, SQQQ for short breakdowns.
Uses core/data.py for reliable data fetching
"""

"""
Base ORB Strategy - Position Aware + Fixed Attribute Error
"""

import pandas as pd
from lumibot.strategies import Strategy
from datetime import datetime, time as dtime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.data import get_price_data
from notifications.emailer import send_email

class ORBStrategy(Strategy):
    parameters = {
        "underlying": "QQQ",
        "bull_ticker": "TQQQ",
        "bear_ticker": "SQQQ",
        "orb_minutes": 15,
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

        # Update position status from Alpaca
        self._update_position_status()

        # EOD Exit
        eod_time = dtime(*map(int, self.parameters["eod_exit_time"].split(":")))
        if now.time() >= eod_time and self.trade_taken:
            self.sell_all()
            self._record_trade("EOD")
            self.trade_taken = False
            return

        if now.time() < dtime(9, 30):
            return

        try:
            df = get_price_data(
                symbol=self.parameters["underlying"],
                api_key=os.getenv("ALPACA_API_KEY"),
                secret_key=os.getenv("ALPACA_API_SECRET"),
                days=3
            )
        except Exception as e:
            self.log_message(f"Data fetch failed: {e}")
            return

        df_today = df[df.index.date == today]
        if len(df_today) < 5:
            return

        if not self.or_established:
            window = df_today.between_time("09:30", "09:45")
            if len(window) >= 3:
                self.or_high = window["high"].max()
                self.or_low = window["low"].min()
                self.or_mid = (self.or_high + self.or_low) / 2
                self.or_established = True
                self.log_message(f"ORB Established | High={self.or_high:.2f} | Low={self.or_low:.2f}")

        if self.trade_taken:
            self._manage_open_trade(df_today)
            return

        if now.time() < dtime(9, 45):
            return

        current = df_today["close"].iloc[-1]

        if current > self.or_high:
            self._enter_position(self.parameters["bull_ticker"], "BULLISH", current)
        elif current < self.or_low:
            self._enter_position(self.parameters["bear_ticker"], "BEARISH", current)

    def _update_position_status(self):
        """Check actual positions from Alpaca"""
        positions = self.get_positions()
        self.trade_taken = False
        self.position_side = None
        self.entry_price = None

        for position in positions:
            symbol = position.asset.symbol
            if symbol in [self.parameters["bull_ticker"], self.parameters["bear_ticker"]]:
                self.trade_taken = True
                self.position_side = "BULLISH" if symbol == self.parameters["bull_ticker"] else "BEARISH"
                
                # Fixed attribute name
                self.entry_price = float(position.avg_fill_price) if position.avg_fill_price else None
                
                self.log_message(f"✅ Detected existing position: {self.position_side} {symbol} | Qty: {position.quantity} | Avg Price: {self.entry_price}")
                break

    def _enter_position(self, ticker, direction, price):
        if self.trade_taken:
            self.log_message("Position already exists. Skipping new entry.")
            return

        risk = abs(price - self.or_mid)
        if risk <= 0:
            return

        qty = int((self.portfolio_value * self.parameters["risk_pct"]) / risk)
        if qty < 1:
            return

        order = self.create_order(ticker, qty, "buy", time_in_force="day")
        self.submit_order(order)

        self.entry_price = price
        self.position_side = direction
        self.trade_taken = True
        self._entry_value = self.portfolio_value

        self.log_message(f"✅ ENTERED {direction} POSITION | {ticker} @ {price:.2f} | Qty={qty} | Risk={risk:.2f}")

        # Email notification
        try:
            subject = f"TRADE EXECUTED: {direction} {ticker}"
            body = f"""
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S EST')}
Symbol: {ticker}
Direction: {direction}
Price: {price:.2f}
Quantity: {qty}
Risk: {risk:.2f}
            """
            send_email(subject, body)
        except:
            pass

    def _reset_daily_state(self, today):
        self.or_high = self.or_low = self.or_mid = None
        self.or_established = False
        self.trade_taken = False
        self.position_side = None
        self._last_date = today

    def _manage_open_trade(self, df_today):
        if len(df_today) == 0:
            return
        current = df_today["close"].iloc[-1]

        if (self.position_side == "BULLISH" and current <= self.or_mid) or \
           (self.position_side == "BEARISH" and current >= self.or_mid):
            self.sell_all()
            self._record_trade("STOP")
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