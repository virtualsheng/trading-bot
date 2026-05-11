
from lumibot.strategies import Strategy

class ORBStrategy(Strategy):

    parameters = {
        "underlying": "QQQ",
        "bull_ticker": "TQQQ",
        "bear_ticker": "SQQQ"
    }

    def initialize(self):

        self.sleeptime = "5M"
        self.set_market("NYSE")

    def on_trading_iteration(self):

        self.log_message("ORB strategy active")
