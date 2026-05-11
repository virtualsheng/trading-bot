"""
Technical Signal Engine - Afternoon Analysis
Momentum + EMA crossover + trend logic
"""

import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data import get_price_data
from core.indicators import compute_ema, compute_rsi, compute_macd
from alpaca.data.timeframe import TimeFrame

def get_technical_signal(symbol, api_key, secret_key):
    try:
        # Use daily bars for trend/momentum analysis
        df = get_price_data(
            symbol=symbol,
            api_key=api_key,
            secret_key=secret_key,
            days=150,                    # Extra buffer
            timeframe=TimeFrame.Day
        )
        
        close = df["close"].dropna()
        
        if len(close) < 200:
            return {"action": "WAIT", "reason": "Not enough history"}

        ema2 = compute_ema(close, 2)
        ema3 = compute_ema(close, 3)
        ema5 = compute_ema(close, 5)
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()
        
        rsi = compute_rsi(close)
        _, _, hist = compute_macd(close)
        
        latest_close = close.iloc[-1]
        
        bull_score = sum([
            ema2.iloc[-1] > ema3.iloc[-1],      # Short momentum
            ema3.iloc[-1] > ema5.iloc[-1],
            latest_close > sma50.iloc[-1],
            latest_close > sma200.iloc[-1],
            hist.iloc[-1] > 0,
            rsi.iloc[-1] < 40
        ])
        
        bear_score = sum([
            ema2.iloc[-1] < ema3.iloc[-1],
            ema3.iloc[-1] < ema5.iloc[-1],
            latest_close < sma50.iloc[-1],
            latest_close < sma200.iloc[-1],
            hist.iloc[-1] < 0,
            rsi.iloc[-1] > 70
        ])
        
        if bull_score >= 5:
            action = "STRONG_BUY"
        elif bull_score >= 4:
            action = "BUY"
        elif bear_score >= 5:
            action = "STRONG_SELL"
        elif bear_score >= 4:
            action = "SELL"
        else:
            action = "HOLD"
        
        return {
            "action": action,
            "bull_score": int(bull_score),
            "bear_score": int(bear_score),
            "rsi": round(float(rsi.iloc[-1]), 2)
        }
        
    except Exception as e:
        return {"action": "ERROR", "error": str(e)}