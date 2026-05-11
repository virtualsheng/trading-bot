import pandas as pd
import pytz
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from datetime import datetime, timedelta


def get_price_data(
    symbol: str,
    api_key: str,
    secret_key: str,
    days: int = 60,
    timeframe: TimeFrame = None
):
    """
    Fetch OHLCV bar data from Alpaca.

    Parameters
    ----------
    symbol   : ticker e.g. "QQQ"
    api_key  : Alpaca API key
    secret_key : Alpaca secret key
    days     : how many calendar days back to fetch
                 - Use 60 for daily technical signals (EMA/RSI/MACD)
                 - Use 2  for intraday ORB signals (5-min bars)
    timeframe: bar size
                 - TimeFrame(1, TimeFrameUnit.Day)    for daily candles
                 - TimeFrame(5, TimeFrameUnit.Minute) for 5-min candles
    """
    if timeframe is None:
        timeframe = TimeFrame(1, TimeFrameUnit.Day)  # default = daily

    try:
        client = StockHistoricalDataClient(api_key, secret_key)

        end   = datetime.now(pytz.UTC)
        start = end - timedelta(days=days)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            adjustment="all"
        )

        bars = client.get_stock_bars(request)
        if not bars or len(bars.df) == 0:
            raise ValueError(f"No bars returned for {symbol}")

        df = bars.df.reset_index()
        df = df[df["symbol"] == symbol].copy()
        if df.empty:
            raise ValueError(f"No data for symbol {symbol}")

        df = df.set_index("timestamp")
        df.index = df.index.tz_convert("US/Eastern")
        return df

    except Exception as e:
        raise Exception(f"Data fetch failed for {symbol}: {str(e)}")


# ── Convenience wrappers so callers don't need to think about parameters ──

def get_daily_bars(symbol: str, api_key: str, secret_key: str, days: int = 60):
    """
    Daily OHLCV bars — for EMA crossovers, RSI, MACD, SMA50/200.
    60 days gives enough history for all daily indicators.
    """
    return get_price_data(
        symbol, api_key, secret_key,
        days=days,
        timeframe=TimeFrame(1, TimeFrameUnit.Day)
    )


def get_intraday_bars(symbol: str, api_key: str, secret_key: str, days: int = 2):
    """
    5-minute OHLCV bars — for Opening Range Breakout.
    2 days is enough to see today's session clearly.
    """
    return get_price_data(
        symbol, api_key, secret_key,
        days=days,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute)
    )