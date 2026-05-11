
import pandas as pd

def compute_ema(series: pd.Series, period: int):
    return series.ewm(
        span=period,
        adjust=False
    ).mean()

def compute_rsi(series: pd.Series, period: int = 14):

    delta = series.diff()

    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()

    rs = gain / loss

    return 100 - (100 / (1 + rs))

def compute_macd(series: pd.Series):

    ema12 = compute_ema(series, 12)
    ema26 = compute_ema(series, 26)

    macd_line = ema12 - ema26

    signal_line = compute_ema(macd_line, 9)

    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram
