"""
Opening Range Breakout (ORB) Strategy
- Defines opening range using first 15 minutes (3 x 5-min bars)
- Enters on 5-min close above/below the range
- Stop loss at midpoint of opening range
- Target = 2x the risk (2:1 reward:risk)
- Exits at end of day if not stopped out

Trades TQQQ for long breakouts, SQQQ for short breakdowns.
"""

from lumibot.strategies import Strategy
from lumibot.entities import Order
from datetime import datetime, time as dtime
import pandas as pd


class ORBStrategy(Strategy):

    parameters = {
        "underlying": "QQQ",      # Symbol to watch for breakout
        "bull_ticker": "TQQQ",    # Long vehicle
        "bear_ticker": "SQQQ",    # Short vehicle
        "orb_minutes": 15,        # Opening range duration in minutes
        "bar_minutes": 5,         # Bar size for entry signal
        "risk_pct": 0.01,         # Risk 1% of portfolio per trade
        "reward_ratio": 2.0,      # Target = 2x the risk
        "eod_exit_time": "15:45", # Exit all positions before close
    }

    def initialize(self):
        # Run on 5-minute bars
        self.sleeptime = "5M"
        self.set_market("NYSE")
        
        # State per trading day
        self.or_high = None
        self.or_low  = None
        self.or_mid  = None
        self.or_established = False
        self.trade_taken = False
        self.entry_price = None
        self.stop_price  = None
        self.target_price = None
        self.position_side = None  # "LONG" or "SHORT"
        self._last_date = None

    def on_trading_iteration(self):
        now = self.get_datetime()
        today = now.date()
        
        # Reset state at start of each new trading day
        if today != self._last_date:
            self.or_high = None
            self.or_low  = None
            self.or_mid  = None
            self.or_established = False
            self.trade_taken = False
            self.entry_price = None
            self.stop_price  = None
            self.target_price = None
            self.position_side = None
            self._last_date = today
            
        underlying = self.parameters["underlying"]
        bull = self.parameters["bull_ticker"]
        bear = self.parameters["bear_ticker"]
        eod  = self.parameters["eod_exit_time"]
        
        # ── EOD Exit ──────────────────────────────────────────────────────
        eod_h, eod_m = map(int, eod.split(":"))
        if now.time() >= dtime(eod_h, eod_m):
            if self.trade_taken:
                self.sell_all()
                self.log_message("EOD exit — closing all positions")
                self.trade_taken = False
            return
        
        # ── Only operate during market hours ──────────────────────────────
        if now.time() < dtime(9, 30):
            return
            
        # ── Get recent 5-min bars ─────────────────────────────────────────
        orb_bars_needed = self.parameters["orb_minutes"] // self.parameters["bar_minutes"]
        bars = self.get_historical_prices(underlying, orb_bars_needed + 5, "5m")
        
        if bars is None or len(bars.df) < orb_bars_needed:
            return
            
        df = bars.df.copy()
        
        # ── Establish Opening Range ────────────────────────────────────────
        if not self.or_established:
            # Use first orb_bars_needed bars of the session
            or_bars = df.iloc[:orb_bars_needed]
            self.or_high = or_bars["high"].max()
            self.or_low  = or_bars["low"].min()
            self.or_mid  = (self.or_high + self.or_low) / 2
            self.or_established = True
            self.log_message(
                f"OR established: High={self.or_high:.2f} "
                f"Low={self.or_low:.2f} Mid={self.or_mid:.2f}"
            )
        
        # ── Skip if already in a trade ────────────────────────────────────
        if self.trade_taken:
            self._manage_open_trade(df)
            return
        
        # ── Check for breakout entry ───────────────────────────────────────
        current_close = df["close"].iloc[-1]
        risk_pct = self.parameters["risk_pct"]
        rr = self.parameters["reward_ratio"]
        
        if current_close > self.or_high:
            # Bullish breakout → buy TQQQ
            stop = self.or_mid
            risk = current_close - stop
            target = current_close + (risk * rr)
            portfolio_risk = self.portfolio_value * risk_pct
            qty = int(portfolio_risk / risk) if risk > 0 else 0
            
            if qty > 0:
                order = self.create_order(bull, qty, "buy")
                self.submit_order(order)
                self.entry_price  = current_close
                self.stop_price   = stop
                self.target_price = target
                self.position_side = "LONG"
                self.trade_taken = True
                self.log_message(
                    f"ORB LONG: {qty} {bull} @ {current_close:.2f} | "
                    f"Stop: {stop:.2f} | Target: {target:.2f}"
                )
                
        elif current_close < self.or_low:
            # Bearish breakdown → buy SQQQ
            stop = self.or_mid
            risk = stop - current_close
            target = current_close - (risk * rr)
            portfolio_risk = self.portfolio_value * risk_pct
            qty = int(portfolio_risk / risk) if risk > 0 else 0
            
            if qty > 0:
                order = self.create_order(bear, qty, "buy")
                self.submit_order(order)
                self.entry_price  = current_close
                self.stop_price   = stop
                self.target_price = target
                self.position_side = "SHORT"
                self.trade_taken = True
                self.log_message(
                    f"ORB SHORT: {qty} {bear} @ {current_close:.2f} | "
                    f"Stop: {stop:.2f} | Target: {target:.2f}"
                )

    def _manage_open_trade(self, df):
        """Check stop and target on open trade."""
        current = df["close"].iloc[-1]
        bull = self.parameters["bull_ticker"]
        bear = self.parameters["bear_ticker"]
        
        if self.position_side == "LONG":
            if current <= self.stop_price:
                self.sell_all()
                self.log_message(f"STOP HIT on LONG @ {current:.2f}")
                self.trade_taken = False
            elif current >= self.target_price:
                self.sell_all()
                self.log_message(f"TARGET HIT on LONG @ {current:.2f}")
                self.trade_taken = False
                
        elif self.position_side == "SHORT":
            if current >= self.stop_price:
                self.sell_all()
                self.log_message(f"STOP HIT on SHORT @ {current:.2f}")
                self.trade_taken = False
            elif current <= self.target_price:
                self.sell_all()
                self.log_message(f"TARGET HIT on SHORT @ {current:.2f}")
                self.trade_taken = False

    def on_abrupt_closing(self):
        self.sell_all()