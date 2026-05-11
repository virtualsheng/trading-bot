import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data import get_price_data
from core.indicators import compute_ema, compute_rsi, compute_macd

def get_technical_signal(symbol, api_key, secret_key):
    try:
        from strategies.data import get_daily_bars
	df = get_daily_bars(symbol, api_key, secret_key)
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
        
        latest = {
            "ema2>ema3": ema2.iloc[-1] > ema3.iloc[-1],
            "ema3>ema5": ema3.iloc[-1] > ema5.iloc[-1],
            "above50": close.iloc[-1] > sma50.iloc[-1],
            "above200": close.iloc[-1] > sma200.iloc[-1],
            "macd_pos": hist.iloc[-1] > 0,
            "rsi_oversold": rsi.iloc[-1] < 40,
            "rsi_overbought": rsi.iloc[-1] > 70,
        }
        
        bull_score = sum([latest["ema2>ema3"], latest["ema3>ema5"], latest["above50"], 
                         latest["above200"], latest["macd_pos"], latest["rsi_oversold"]])
        bear_score = sum([not latest["ema2>ema3"], not latest["ema3>ema5"], not latest["above50"],
                         not latest["above200"], not latest["macd_pos"], latest["rsi_overbought"]])
        
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