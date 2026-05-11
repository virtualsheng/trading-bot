import pandas as pd
import numpy as np
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta

def get_price_data(symbol: str, api_key: str, secret_key: str, 
                   days: int = 60, timeframe=TimeFrame.Day):
    """Fetch OHLCV data from Alpaca."""
    client = StockHistoricalDataClient(api_key, secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=datetime.now() - timedelta(days=days),
        end=datetime.now()
    )
    bars = client.get_stock_bars(request)
    df = bars.df.reset_index()
    df = df[df['symbol'] == symbol].copy()
    df = df.set_index('timestamp')
    return df

def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
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

def get_technical_signal(symbol: str, api_key: str, secret_key: str) -> dict:
    """
    Generate a Cory-style technical signal for a symbol.
    Returns: dict with action, strength, indicators
    """
    try:
        df = get_price_data(symbol, api_key, secret_key, days=60)
        close = df['close']
        
        # --- Moving Average Crossovers (Cory's primary tool) ---
        ema2 = compute_ema(close, 2)
        ema3 = compute_ema(close, 3)
        ema5 = compute_ema(close, 5)
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()
        
        # Short crossover: 2/3 EMA
        short_cross_bullish = (ema2.iloc[-1] > ema3.iloc[-1] and 
                                ema2.iloc[-2] <= ema3.iloc[-2])
        short_cross_bearish = (ema2.iloc[-1] < ema3.iloc[-1] and 
                                ema2.iloc[-2] >= ema3.iloc[-2])
        
        # Medium crossover: 3/5 EMA  
        med_cross_bullish = (ema3.iloc[-1] > ema5.iloc[-1] and 
                              ema3.iloc[-2] <= ema5.iloc[-2])
        med_cross_bearish = (ema3.iloc[-1] < ema5.iloc[-1] and 
                              ema3.iloc[-2] >= ema5.iloc[-2])
        
        # Trend regime
        above_sma50 = close.iloc[-1] > sma50.iloc[-1]
        above_sma200 = close.iloc[-1] > sma200.iloc[-1]
        golden_cross = sma50.iloc[-1] > sma200.iloc[-1]
        
        # --- RSI ---
        rsi = compute_rsi(close)
        rsi_val = rsi.iloc[-1]
        rsi_signal = "BUY" if rsi_val < 40 else "SELL" if rsi_val > 70 else "NEUTRAL"
        
        # --- MACD ---
        macd_line, signal_line, histogram = compute_macd(close)
        macd_bullish = (histogram.iloc[-1] > 0 and histogram.iloc[-1] > histogram.iloc[-2])
        macd_bearish = (histogram.iloc[-1] < 0 and histogram.iloc[-1] < histogram.iloc[-2])
        
        # --- Score bullish vs bearish signals ---
        bull_score = sum([
            short_cross_bullish,
            med_cross_bullish,
            above_sma50,
            above_sma200,
            golden_cross,
            macd_bullish,
            rsi_signal == "BUY"
        ])
        
        bear_score = sum([
            short_cross_bearish,
            med_cross_bearish,
            not above_sma50,
            not above_sma200,
            not golden_cross,
            macd_bearish,
            rsi_signal == "SELL"
        ])
        
        # --- Generate signal ---
        if bull_score >= 5:
            action = "BUY"
            strength = "STRONG" if bull_score >= 6 else "MODERATE"
        elif bear_score >= 5:
            action = "SELL"
            strength = "STRONG" if bear_score >= 6 else "MODERATE"
        else:
            action = "HOLD"
            strength = "WEAK"
        
        return {
            "symbol": symbol,
            "action": action,
            "strength": strength,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "rsi": round(rsi_val, 2),
            "macd_bullish": macd_bullish,
            "above_sma50": above_sma50,
            "above_sma200": above_sma200,
            "golden_cross": golden_cross,
            "ema_cross_short": "BULL" if short_cross_bullish else "BEAR" if short_cross_bearish else "NONE",
            "ema_cross_med": "BULL" if med_cross_bullish else "BEAR" if med_cross_bearish else "NONE",
        }
        
    except Exception as e:
        return {"symbol": symbol, "action": "HOLD", "strength": "WEAK", 
                "error": str(e)}