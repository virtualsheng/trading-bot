"""
Trend-Filtered ORB Strategy
Afternoon Technical Signal (previous day) = Primary Bias
"""

import os
from datetime import datetime, timedelta
from lumibot.strategies import Strategy
from strategies.orb_strategy import ORBStrategy
from strategies.signal_engine import get_technical_signal

class TrendFilteredORB(ORBStrategy):

    def initialize(self):
        super().initialize()
        self.daily_bias = "NEUTRAL"   # LONG_BIAS, SHORT_BIAS, NEUTRAL
        self.last_bias_date = None

    def before_market_opens(self):
        """Load previous day's technical signal as primary bias"""
        today = datetime.now().date()
        if today == self.last_bias_date:
            return

        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_API_SECRET")

        if not api_key or not secret_key:
            self.log_message("⚠️ Missing credentials for trend filter")
            return

        self.log_message("=== Loading Previous Day Technical Bias (Primary Signal) ===")
        
        trend = get_technical_signal(self.parameters["underlying"], api_key, secret_key)
        action = trend.get("action", "HOLD")

        if action in ["STRONG_SELL", "SELL"]:
            self.daily_bias = "SHORT_BIAS"
            self.parameters["allow_long"] = False
            self.parameters["allow_short"] = True
            self.log_message(f"🔻 BEARISH BIAS from previous day → Only SHORT trades allowed today")
        elif action in ["STRONG_BUY", "BUY"]:
            self.daily_bias = "LONG_BIAS"
            self.parameters["allow_long"] = True
            self.parameters["allow_short"] = False
            self.log_message(f"🚀 BULLISH BIAS from previous day → Only LONG trades allowed today")
        else:
            self.daily_bias = "NEUTRAL"
            self.parameters["allow_long"] = True
            self.parameters["allow_short"] = False
            self.log_message(f"⚖️ NEUTRAL bias → Only LONG trades allowed (conservative)")

        self.last_bias_date = today

    def on_trading_iteration(self):
        # Enforce previous day's technical bias
        if self.daily_bias == "SHORT_BIAS" and self.parameters.get("allow_long", True):
            return  # Block long trades
        if self.daily_bias == "LONG_BIAS" and self.parameters.get("allow_short", True):
            return  # Block short trades

        super().on_trading_iteration()