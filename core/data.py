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
    if timeframe is None:
        timeframe = TimeFrame(5, TimeFrameUnit.Minute)

    # Try Alpaca first
    if api_key and secret_key:
        try:
            client = StockHistoricalDataClient(api_key, secret_key)
            end = datetime.now(pytz.UTC)
            start = end - timedelta(days=days + 2)
            
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
                return df
        except:
            pass  # fallback to yfinance

    # yfinance fallback
    try:
        interval = "5m" if timeframe == TimeFrame(5, TimeFrameUnit.Minute) else "1d"
        df = yf.download(symbol, period=f"{days+5}d", interval=interval, progress=False, prepost=True)
        
        if df.empty:
            raise ValueError("No data")
            
        df = df.rename(columns={'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'})
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("US/Eastern")
        else:
            df.index = df.index.tz_convert("US/Eastern")
        return df
    except Exception as e:
        raise Exception(f"Both sources failed for {symbol}: {str(e)}")