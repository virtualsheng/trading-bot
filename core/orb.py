import pytz
from datetime import datetime
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from core.data import get_price_data

def get_orb_signal(symbol, api_key, secret_key):
    try:
        df = get_price_data(
            symbol=symbol,
            api_key=api_key,
            secret_key=secret_key,
            days=3,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute)
        )
        
        est = pytz.timezone("US/Eastern")
        today = datetime.now(est).date()
        
        df_today = df[df.index.date == today]
        
        if len(df_today) < 3:
            return {"signal": "WAIT", "reason": f"Insufficient bars ({len(df_today)})"}

        opening_range = df_today.between_time("09:30", "09:45")
        
        if len(opening_range) < 3:
            current = df_today["close"].iloc[-1]
            return {
                "signal": "WAIT",
                "current": round(float(current), 2),
                "reason": "Opening range still forming"
            }
        
        or_high = opening_range["high"].max()
        or_low = opening_range["low"].min()
        current = df_today["close"].iloc[-1]
        open_price = df_today["open"].iloc[0]
        
        # Additional metrics
        pct_change_open = ((current - open_price) / open_price * 100)
        avg_vol = df_today["volume"].rolling(5).mean().iloc[-1] if len(df_today) > 5 else df_today["volume"].mean()
        latest_vol = df_today["volume"].iloc[-1]
        vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 1.0

        if current > or_high:
            signal = "BUY"
            reason = "Breakout Above OR"
        elif current < or_low:
            signal = "SELL"
            reason = "Breakdown Below OR"
        else:
            signal = "WAIT"
            reason = "Inside OR"

        return {
            "signal": signal,
            "current": round(float(current), 2),
            "or_high": round(float(or_high), 2),
            "or_low": round(float(or_low), 2),
            "pct_change_open": round(float(pct_change_open), 2),
            "volume_ratio": round(float(vol_ratio), 2),
            "reason": reason
        }
        
    except Exception as e:
        return {"signal": "ERROR", "reason": str(e)[:100]}