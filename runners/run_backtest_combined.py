import os
import pandas as pd
from datetime import datetime
from lumibot.backtesting import PolygonDataBacktesting, PandasDataBacktesting
from lumibot.entities import Asset, Data
from strategies.orb_strategy import ORBStrategy
from dotenv import load_dotenv

load_dotenv()

def get_cached_data(symbol, start, end):
    cache_dir = "cache"
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
    
    file_path = os.path.join(cache_dir, f"{symbol}_{start.date()}_{end.date()}.csv")

    if os.path.exists(file_path):
        print(f"✅ Loading {symbol} from local cache...")
        return pd.read_csv(file_path, index_col=0, parse_dates=True)
    
    print(f"🌐 Fetching {symbol} from Polygon (First time only)...")
    # Initialize Polygon source
    polygon = PolygonDataBacktesting(
        datetime(2024, 1, 1), datetime(2024, 1, 2), 
        api_key=os.getenv("POLYGON_API_KEY")
    )
    
    # Create Asset object
    asset = Asset(symbol=symbol, asset_type="stock")
    
    # Fetch using correct method name
    data_obj = polygon.get_historical_prices(
        asset, 
        timestep="minute", 
        start_date=start, 
        end_date=end
    )
    
    # Extract dataframe from Data object
    df = data_obj.df
    df.to_csv(file_path)
    return df

if __name__ == "__main__":
    SYMBOL = "QQQ"
    TICKERS = [SYMBOL, "TQQQ", "SQQQ"] # We need all three for the strategy to work
    START = datetime(2024, 7, 1)
    END = datetime(2026, 5, 11)

    pandas_data = {}

    # Load/Cache all required assets
    for ticker in TICKERS:
        df = get_cached_data(ticker, START, END)
        asset = Asset(symbol=ticker, asset_type="stock")
        # Important: PandasDataBacktesting needs {Asset: Data}
        pandas_data[asset] = Data(asset, df, timestep="minute")

    print("🚀 Starting Backtest...")
    ORBStrategy.run_backtest(
        datasource_class=PandasDataBacktesting,
        backtesting_start=START,
        backtesting_end=END,
        budget=100000, 
        pandas_data=pandas_data,
        parameters={
            "underlying": SYMBOL,
            "bull_ticker": "TQQQ",
            "bear_ticker": "SQQQ",
            "orb_minutes": 15,
            "bar_minutes": 5,
            "risk_pct": 0.01,
            "reward_ratio": 2.0,
            "eod_exit_time": "15:45"
        }
    )