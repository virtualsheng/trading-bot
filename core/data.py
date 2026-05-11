
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta

def get_price_data(
    symbol: str,
    api_key: str,
    secret_key: str,
    days: int = 60,
    timeframe=TimeFrame.Day
):

    client = StockHistoricalDataClient(
        api_key,
        secret_key
    )

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=datetime.now() - timedelta(days=days),
        end=datetime.now()
    )

    bars = client.get_stock_bars(request)

    df = bars.df.reset_index()

    df = df[df["symbol"] == symbol].copy()

    df = df.set_index("timestamp")

    return df
