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
        if timeframe == TimeFrame.Day:
            # FIX: Fetch 1m data for the last 2 days to ensure today's "live" price is included
            # This is crucial for the 3:50 PM signal accuracy.
            hist = yf.download(symbol, period=f"{days+5}d", interval="1d", progress=False)
            live_data = yf.download(symbol, period="1d", interval="1m", progress=False, prepost=True)
            
            if not live_data.empty:
                # Resample 1m data into a single daily row for today
                today_bar = live_data.resample('D').agg({
                    'Open': 'first',
                    'High': 'max',
                    'Low': 'min',
                    'Close': 'last',
                    'Volume': 'sum'
                }).dropna()
                
                # Update or append today's bar to history
                df = pd.concat([hist[~hist.index.isin(today_bar.index)], today_bar])
            else:
                df = hist
        else:
            interval = "5m"
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
        print(f"Error fetching data for {symbol}: {e}")
        return pd.DataFrame()