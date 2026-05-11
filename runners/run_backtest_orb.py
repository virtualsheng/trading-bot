"""
Run a backtest of the Opening Range Breakout strategy.
NOTE: ORB uses 5-minute bars. Yahoo Finance free tier provides
intraday data for the last 60 days only.

For longer backtests (1-2 years), you need:
- Alpaca free data API (market hours, limited history)
- Polygon.io free tier (recommended — use code LUMI10 for 10% off)

For now this runs a 30-day backtest using Yahoo intraday data.
"""
# THIS MUST BE FIRST — before any lumibot imports
import os
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from lumibot.backtesting import PolygonDataBacktesting
from strategies.orb_strategy import ORBStrategy

# Using Polygon intraday
END   = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
START = END - timedelta(days=365)

PARAMS = {
    "underlying":     "QQQ",
    "bull_ticker":    "TQQQ",
    "bear_ticker":    "SQQQ",
    "orb_minutes":    15,
    "bar_minutes":    5,
    "risk_pct":       0.01,    # Risk 1% per trade
    "reward_ratio":   2.0,     # 2:1 target
    "eod_exit_time":  "15:45",
}

if __name__ == "__main__":
    result = ORBStrategy.run_backtest(
        datasource_class=PolygonDataBacktesting,
        backtesting_start=START,
        backtesting_end=END,
        parameters=PARAMS,
        benchmark_asset="QQQ",
        show_plot=True,
        show_tearsheet=True,
        initial_portfolio_value=2000,
    )
    
    print("\n" + "="*50)
    print("ORB BACKTEST RESULTS (30-day)")
    print("="*50)
    if result:
        print(f"Total Return:     {result.get('total_return', 'N/A'):.2%}")
        print(f"Benchmark:        {result.get('benchmark_return', 'N/A'):.2%}")
        print(f"Sharpe Ratio:     {result.get('sharpe', 'N/A'):.2f}")
        print(f"Max Drawdown:     {result.get('max_drawdown', 'N/A'):.2%}")
        print(f"Win Rate:         {result.get('win_rate', 'N/A'):.2%}")
        print(f"Total Trades:     {result.get('total_trades', 'N/A')}")