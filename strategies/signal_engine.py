"""
Technical Signal Engine - Afternoon Analysis
Momentum + EMA crossover + trend logic
"""

import os
import sys
import warnings
import math
warnings.filterwarnings("ignore", category=RuntimeWarning)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data import get_price_data
from core.indicators import compute_ema, compute_rsi, compute_macd
from alpaca.data.timeframe import TimeFrame
import pandas as pd
from datetime import datetime

def get_technical_signal(symbol, api_key, secret_key):
    try:
        df = get_price_data(
            symbol=symbol,
            api_key=api_key,
            secret_key=secret_key,
            days=400,
            timeframe=TimeFrame.Day
        )

        close = df["close"].dropna()
        volume = df["volume"].dropna()
        open_price = df["open"].dropna()

        if len(close) < 120:
            return {"action": "WAIT", "reason": f"Insufficient history ({len(close)} bars)"}

        ema2 = compute_ema(close, 2)
        ema3 = compute_ema(close, 3)
        ema5 = compute_ema(close, 5)
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()

        rsi = compute_rsi(close)
        _, _, hist = compute_macd(close)

        latest_close = close.iloc[-1]
        latest_open = open_price.iloc[-1] if len(open_price) > 0 else latest_close
        latest_rsi = rsi.iloc[-1]

        # ── Volume ratio with NaN guard ───────────────────────────────────
        avg_volume = volume.rolling(20).mean().iloc[-1]
        latest_volume = volume.iloc[-1]

        # Guard against NaN values on thin/new symbols
        if (math.isnan(float(latest_volume)) or math.isnan(float(avg_volume))
                or float(avg_volume) <= 0):
            volume_ratio = 1.0
        else:
            volume_ratio = float(latest_volume) / float(avg_volume)

        short_momentum_bull = (ema2.iloc[-1] > ema3.iloc[-1]) and (ema3.iloc[-1] > ema5.iloc[-1])
        short_momentum_bear = (ema2.iloc[-1] < ema3.iloc[-1]) and (ema3.iloc[-1] < ema5.iloc[-1])

        bull_score = sum([short_momentum_bull, latest_close > sma50.iloc[-1],
                         latest_close > sma200.iloc[-1], hist.iloc[-1] > 0, latest_rsi < 45])
        bear_score = sum([short_momentum_bear, latest_close < sma50.iloc[-1],
                         latest_close < sma200.iloc[-1], hist.iloc[-1] < 0, latest_rsi > 60])

        pct_change_open = ((latest_close - latest_open) / latest_open * 100) if latest_open else 0

        if bull_score >= 5 and latest_rsi < 68 and volume_ratio > 1.1:
            action = "STRONG_BUY"
        elif bull_score >= 4 and latest_rsi < 62:
            action = "BUY"
        elif bear_score >= 5 and latest_rsi > 32 and volume_ratio > 1.1:
            action = "STRONG_SELL"
        elif bear_score >= 4 and latest_rsi > 38:
            action = "SELL"
        else:
            action = "HOLD"

        # Log daily signal
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        log_file = f"{log_dir}/daily_signals.log"

        with open(log_file, "a") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | {symbol} | {action} | RSI={round(latest_rsi,2)} | Bull={bull_score} Bear={bear_score}\n")

        return {
            "action": action,
            "bull_score": int(bull_score),
            "bear_score": int(bear_score),
            "rsi": round(float(latest_rsi), 2),
            "rsi_interpretation": "Oversold" if latest_rsi < 35 else "Overbought" if latest_rsi > 65 else "Neutral",
            "pct_change_open": round(float(pct_change_open), 2),
            "volume_ratio": round(float(volume_ratio), 2),
            "above_sma200": bool(latest_close > sma200.iloc[-1])
        }

    except Exception as e:
        return {"action": "ERROR", "error": str(e)}