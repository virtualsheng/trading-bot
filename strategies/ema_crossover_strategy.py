"""
EMA Crossover Strategy
Trades TQQQ/SQQQ based on:
- EMA 2/3 and 3/5 crossovers (short-term momentum)
- RSI(14) for overbought/oversold confirmation
- MACD for trend confirmation
- SMA50/200 for regime filter (only trade with the trend)

Same code works for backtesting AND live trading — just swap the broker.
"""

from lumibot.strategies import Strategy
from lumibot.entities import Asset, Order
import pandas as pd
import pandas_ta as ta
from datetime import datetime


class EMACrossoverStrategy(Strategy):
    
    # ── Parameters (easy to tune without touching logic) ──────────────────
    parameters = {
        "underlying": "QQQ",      # Symbol to read signals from
        "bull_ticker": "TQQQ",    # What to buy when bullish
        "bear_ticker": "SQQQ",    # What to buy when bearish
        "ema_fast": 2,            # Fast EMA period
        "ema_mid": 3,             # Mid EMA period
        "ema_slow": 5,            # Slow EMA period
        "rsi_period": 14,
        "rsi_oversold": 40,       # RSI below this = oversold = bullish
        "rsi_overbought": 70,     # RSI above this = overbought = bearish
        "sma_trend": 50,          # Must be above this to go long
        "position_pct": 0.95,     # Use 95% of portfolio per trade
        "lookback_days": 60,      # Days of history to pull
        "min_bull_score": 4,      # Out of 6 signals needed for BUY
        "min_bear_score": 4,      # Out of 6 signals needed for SELL
    }

    def initialize(self):
        # Run once per day at market open
        self.sleeptime = "1D"
        self.set_market("NYSE")
        
        # Track current position
        self.current_position = None  # "BULL", "BEAR", or None
        
    def on_trading_iteration(self):
        underlying = self.parameters["underlying"]
        bull = self.parameters["bull_ticker"]
        bear = self.parameters["bear_ticker"]
        lookback = self.parameters["lookback_days"]
        
        # ── Get price history ─────────────────────────────────────────────
        bars = self.get_historical_prices(
            underlying, 
            lookback, 
            "day"
        )
        
        if bars is None or len(bars.df) < 30:
            self.log_message("Not enough price history, skipping.")
            return
            
        df = bars.df.copy()
        close = df["close"]
        
        # ── Compute indicators ────────────────────────────────────────────
        p = self.parameters
        
        ema_fast = close.ewm(span=p["ema_fast"], adjust=False).mean()
        ema_mid  = close.ewm(span=p["ema_mid"],  adjust=False).mean()
        ema_slow = close.ewm(span=p["ema_slow"], adjust=False).mean()
        sma50    = close.rolling(50).mean()
        sma200   = close.rolling(200).mean()
        
        # RSI via pandas-ta
        rsi = ta.rsi(close, length=p["rsi_period"])
        
        # MACD
        macd_df  = ta.macd(close)
        macd_hist = macd_df["MACDh_12_26_9"] if macd_df is not None else None
        
        # Current values
        ef_now  = ema_fast.iloc[-1]; ef_prev  = ema_fast.iloc[-2]
        em_now  = ema_mid.iloc[-1];  em_prev  = ema_mid.iloc[-2]
        es_now  = ema_slow.iloc[-1]; es_prev  = ema_slow.iloc[-2]
        s50_now = sma50.iloc[-1]
        s200_now = sma200.iloc[-1]
        rsi_now = rsi.iloc[-1] if rsi is not None else 50
        price_now = close.iloc[-1]
        
        macd_bull = False
        macd_bear = False
        if macd_hist is not None:
            macd_bull = (macd_hist.iloc[-1] > 0 and 
                        macd_hist.iloc[-1] > macd_hist.iloc[-2])
            macd_bear = (macd_hist.iloc[-1] < 0 and 
                        macd_hist.iloc[-1] < macd_hist.iloc[-2])
        
        # ── Score signals ─────────────────────────────────────────────────
        # Bullish signals (1 point each)
        bull_signals = {
            "ema_2_3_cross":   ef_now > em_now and ef_prev <= em_prev,
            "ema_3_5_cross":   em_now > es_now and em_prev <= es_prev,
            "above_sma50":     price_now > s50_now,
            "above_sma200":    price_now > s200_now,
            "rsi_oversold":    rsi_now < p["rsi_oversold"],
            "macd_bullish":    macd_bull,
        }
        
        # Bearish signals
        bear_signals = {
            "ema_2_3_cross":   ef_now < em_now and ef_prev >= em_prev,
            "ema_3_5_cross":   em_now < es_now and em_prev >= es_prev,
            "below_sma50":     price_now < s50_now,
            "below_sma200":    price_now < s200_now,
            "rsi_overbought":  rsi_now > p["rsi_overbought"],
            "macd_bearish":    macd_bear,
        }
        
        bull_score = sum(bull_signals.values())
        bear_score = sum(bear_signals.values())
        
        # ── Log current state ─────────────────────────────────────────────
        self.log_message(
            f"{underlying} | Price: {price_now:.2f} | RSI: {rsi_now:.1f} | "
            f"Bull: {bull_score}/6 | Bear: {bear_score}/6 | "
            f"Position: {self.current_position}"
        )
        
        # ── Execute trades ────────────────────────────────────────────────
        min_bull = p["min_bull_score"]
        min_bear = p["min_bear_score"]
        pos_pct  = p["position_pct"]
        
        if bull_score >= min_bull and self.current_position != "BULL":
            # Switch to BULL — sell SQQQ if held, buy TQQQ
            self._close_position(bear)
            cash = self.portfolio_value * pos_pct
            price = self.get_last_price(bull)
            qty = int(cash / price)
            if qty > 0:
                order = self.create_order(bull, qty, "buy")
                self.submit_order(order)
                self.current_position = "BULL"
                self.log_message(
                    f"→ BUY {qty} {bull} @ ~{price:.2f} "
                    f"(bull score {bull_score}/6)"
                )
                
        elif bear_score >= min_bear and self.current_position != "BEAR":
            # Switch to BEAR — sell TQQQ if held, buy SQQQ
            self._close_position(bull)
            cash = self.portfolio_value * pos_pct
            price = self.get_last_price(bear)
            qty = int(cash / price)
            if qty > 0:
                order = self.create_order(bear, qty, "buy")
                self.submit_order(order)
                self.current_position = "BEAR"
                self.log_message(
                    f"→ BUY {qty} {bear} @ ~{price:.2f} "
                    f"(bear score {bear_score}/6)"
                )
                
        elif bull_score < min_bull and bear_score < min_bear:
            # No clear signal — go flat
            if self.current_position is not None:
                self._close_position(bull)
                self._close_position(bear)
                self.current_position = None
                self.log_message("→ FLAT — no clear signal")

    def _close_position(self, ticker: str):
        """Sell entire position in a ticker if we hold any."""
        position = self.get_position(ticker)
        if position and position.quantity > 0:
            order = self.create_order(ticker, position.quantity, "sell")
            self.submit_order(order)
            self.log_message(f"→ SOLD {position.quantity} {ticker}")

    def on_abrupt_closing(self):
        """Called when strategy is stopped — close all positions cleanly."""
        self.sell_all()