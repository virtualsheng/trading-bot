import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from datetime import datetime, timedelta

def get_orb_signal(symbol, api_key, secret_key):
    try:
        df = get_price_data(
            symbol=symbol,
            api_key=api_key,
            secret_key=secret_key,
            days=3,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute)   # Make sure this import exists
        )
        
        est = pytz.timezone("US/Eastern")
        now_est = datetime.now(est)
        today = now_est.date()
        
        df_today = df[df.index.date == today]
        
        print(f"Debug {symbol}: {len(df_today)} bars today (last: {df_today.index[-1] if not df_today.empty else 'None'})")
        
        if len(df_today) < 3:
            return {
                "signal": "WAIT",
                "current": None,
                "or_high": None,
                "or_low": None,
                "reason": f"Market not open yet or insufficient data ({len(df_today)} bars)"
            }
        
        # Opening Range: 9:30 - 9:45 ET
        opening_range = df_today.between_time("09:30", "09:45")
        
        if len(opening_range) < 3:
            return {
                "signal": "WAIT",
                "current": round(float(df_today["close"].iloc[-1]), 2),
                "or_high": None,
                "or_low": None,
                "reason": f"Opening range still forming ({len(opening_range)} bars)"
            }
        
        or_high = opening_range["high"].max()
        or_low = opening_range["low"].min()
        or_mid = (or_high + or_low) / 2
        current_price = df_today["close"].iloc[-1]
        
        if current_price > or_high:
            signal = "BUY"
            reason = "Above OR High"
        elif current_price < or_low:
            signal = "SELL"
            reason = "Below OR Low"
        else:
            signal = "WAIT"
            reason = "Inside OR"
        
        return {
            "signal": signal,
            "current": round(float(current_price), 2),
            "or_high": round(float(or_high), 2),
            "or_low": round(float(or_low), 2),
            "stop_loss": round(float(or_mid), 2),
            "reason": reason
        }
        
    except Exception as e:
        return {
            "signal": "ERROR",
            "current": None,
            "or_high": None,
            "or_low": None,
            "reason": str(e)[:100]
        }