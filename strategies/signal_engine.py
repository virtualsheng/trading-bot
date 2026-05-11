
from core.data import get_price_data
from core.indicators import (
    compute_ema,
    compute_rsi,
    compute_macd
)

def get_technical_signal(
    symbol,
    api_key,
    secret_key
):

    try:

        df = get_price_data(
            symbol=symbol,
            api_key=api_key,
            secret_key=secret_key,
            days=60
        )

        close = df["close"]

        ema2 = compute_ema(close, 2)
        ema3 = compute_ema(close, 3)
        ema5 = compute_ema(close, 5)

        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()

        rsi = compute_rsi(close)
        rsi_val = rsi.iloc[-1]

        _, _, histogram = compute_macd(close)

        bull_score = sum([
            ema2.iloc[-1] > ema3.iloc[-1],
            ema3.iloc[-1] > ema5.iloc[-1],
            close.iloc[-1] > sma50.iloc[-1],
            close.iloc[-1] > sma200.iloc[-1],
            histogram.iloc[-1] > 0,
            rsi_val < 40
        ])

        bear_score = sum([
            ema2.iloc[-1] < ema3.iloc[-1],
            ema3.iloc[-1] < ema5.iloc[-1],
            close.iloc[-1] < sma50.iloc[-1],
            close.iloc[-1] < sma200.iloc[-1],
            histogram.iloc[-1] < 0,
            rsi_val > 70
        ])

        if bull_score >= 4:
            action = "BUY"
        elif bear_score >= 4:
            action = "SELL"
        else:
            action = "HOLD"

        return {
            "symbol": symbol,
            "action": action,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "rsi": round(float(rsi_val), 2)
        }

    except Exception as e:

        return {
            "symbol": symbol,
            "action": "ERROR",
            "error": str(e)
        }
