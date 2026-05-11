from strategies.orb_strategy import ORBStrategy
from strategies.signal_engine import get_technical_signal
import os

class TrendFilteredORB(ORBStrategy):
    def before_market_opens(self):
        """
        The Priority Rule: Fetch the trend signal before the market starts.
        """
        self.or_high = None # Reset for new day
        self.or_low = None
        
        # Use existing signal engine to check trend on the underlying (e.g., QQQ)
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_API_SECRET")
        
        trend_data = get_technical_signal(self.parameters["underlying"], api_key, secret_key)
        action = trend_data.get("action", "HOLD")
        
        self.log_message(f"Strategic Trend Filter for today: {action}")

        # Apply the Hierarchy
        if action in ["STRONG_BUY", "BUY"]:
            self.parameters["allow_long"] = True
            self.parameters["allow_short"] = False
        elif action in ["STRONG_SELL", "SELL"]:
            self.parameters["allow_long"] = False
            self.parameters["allow_short"] = True
        else:
            # If Neutral, allow both or disable both based on your risk tolerance
            self.parameters["allow_long"] = True
            self.parameters["allow_short"] = True