
from alpaca.data.timeframe import TimeFrame
from core.data import get_price_data

def get_orb_signal(symbol, api_key, secret_key):

    try:

        df = get_price_data(
            symbol=symbol,
            api_key=api_key,
            secret_key=secret_key,
            days=1,
            timeframe=TimeFrame.Minute
        )

        df = df.between_time("09:30", "10:00")

        if len(df) < 15:
            return {
                "signal": "WAIT",
                "reason": "Not enough data"
            }

        opening_range = df.iloc[:15]

        or_high = opening_range["high"].max()
        or_low = opening_range["low"].min()

        or_mid = (or_high + or_low) / 2

        current = df["close"].iloc[-1]

        if current > or_high:

            return {
                "signal": "BUY",
                "or_high": float(or_high),
                "or_low": float(or_low),
                "current": float(current),
                "stop_loss": float(or_mid)
            }

        elif current < or_low:

            return {
                "signal": "SELL",
                "or_high": float(or_high),
                "or_low": float(or_low),
                "current": float(current),
                "stop_loss": float(or_mid)
            }

        return {
            "signal": "WAIT",
            "or_high": float(or_high),
            "or_low": float(or_low),
            "current": float(current),
            "reason": "Inside opening range"
        }

    except Exception as e:

        return {
            "signal": "ERROR",
            "reason": str(e)
        }
