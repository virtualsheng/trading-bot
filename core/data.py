from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from datetime import datetime, timedelta
import pandas as pd
import pytz
import yfinance as yf

def get_price_data(
    symbol: str,
    api_key: str = None,
    secret_key: str = None,
    days: int = 5,
    timeframe=None
):
    """
    Try Alpaca first (with IEX), fallback to yfinance if it fails.
    """
    # Default timeframe
    if timeframe is None:
        timeframe = TimeFrame(5, TimeFrameUnit.Minute)

    # === Try Alpaca First ===
    if api_key and secret_key:
        try:
            client = StockHistoricalDataClient(api_key, secret_key)
            
            end = datetime.now(pytz.UTC)
            start = end - timedelta(days=days)
            
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                adjustment="all",
                feed="iex"
            )
            
            bars = client.get_stock_bars(request)
            if bars and len(bars.df) > 0:
                df = bars.df.reset_index()
                df = df[df["symbol"] == symbol].copy()
                df = df.set_index("timestamp")
                df.index = df.index.tz_convert("US/Eastern")
                print(f"✅ {symbol}: Data from Alpaca IEX")
                return df
        except Exception as e:
            print(f"⚠️ Alpaca failed for {symbol}, trying yfinance... ({str(e)[:80]})")

    # === Fallback to yfinance ===
    try:
        # Convert timeframe for yfinance
        interval_map = {
            TimeFrame(5, TimeFrameUnit.Minute): "5m",
            TimeFrame.Day: "1d",
        }
        interval = interval_map.get(timeframe, "5m")
        
        end = datetime.now(pytz.UTC)
        start = end - timedelta(days=days + 1)
        
        df = yf.download(
            symbol,
            start=start,
            end=end,
            interval=interval,
            progress=False,
            prepost=True  # Include pre-market if available
        )
        
        if df.empty:
            raise ValueError("No data from yfinance")
        
        df = df.rename(columns={
            'Open': 'open', 'High': 'high',
            'Low': 'low', 'Close': 'close', 'Volume': 'volume'
        })
        
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("US/Eastern")
        else:
            df.index = df.index.tz_convert("US/Eastern")
        
        print(f"✅ {symbol}: Data from yfinance (fallback)")
        return df
        
    except Exception as e:
        raise Exception(f"Both Alpaca and yfinance failed for {symbol}: {str(e)}")