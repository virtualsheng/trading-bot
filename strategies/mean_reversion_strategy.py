"""
mean_reversion_strategy.py — Regime-Based Mean Reversion
──────────────────────────────────────────────────────────
Activated when the AI regime detector classifies the market as:
  - "ranging"        → price oscillating in a band
  - "mean_reversion" → price extended from MA, likely to snap back

Strategy logic:
  - In a RANGING market, ORB breakouts frequently fail (price reverses
    back inside the range). This strategy does the opposite: it fades
    breakouts by entering against the breakout direction when:
      1. Regime is ranging/mean_reversion (from AI regime detector)
      2. Price has broken outside the OR by more than the "extension threshold"
      3. RSI confirms overextension (>70 for shorts, <30 for longs)

  - Entry: fade the breakout (buy if price broke down, sell if broke up)
  - Stop:  beyond the extension (OR boundary + 1x ATR)
  - Target: OR midpoint (mean reversion target)

This is intentionally conservative — it only fires when regime confidence
is high (>0.70) and RSI confirms the overextension. Low conviction = skip.

Integration: TrendFilteredORB calls this automatically when regime
detection returns ranging/mean_reversion. No separate runner needed.
"""

import pandas as pd
import numpy as np
from typing import Optional


def should_use_mean_reversion(regime: dict, min_confidence: float = 0.70) -> bool:
    """
    Returns True if the current regime warrants mean-reversion entries
    instead of ORB momentum entries.
    """
    if not regime:
        return False
    regime_type = regime.get("regime", "unknown")
    confidence  = regime.get("confidence", 0.0)
    return (
        regime_type in ("ranging", "mean_reversion")
        and confidence >= min_confidence
    )


def get_mean_reversion_signal(
    df_today: pd.DataFrame,
    or_high: float,
    or_low: float,
    or_mid: float,
    regime: dict,
    rsi_current: float,
    atr: float,
    extension_threshold_pct: float = 0.003,  # 0.3% beyond OR boundary
) -> Optional[dict]:
    """
    Evaluate whether a mean-reversion entry is valid right now.

    Parameters
    ----------
    df_today             : today's 5-min bars
    or_high / or_low     : opening range boundaries
    or_mid               : opening range midpoint (our target)
    regime               : result from detect_regime()
    rsi_current          : current RSI(14) value
    atr                  : current ATR(14)
    extension_threshold_pct : how far beyond OR (as % of or_mid) before entry

    Returns
    -------
    dict with keys: direction, entry, stop, target, reason
    or None if no valid setup
    """
    if df_today.empty:
        return None

    current = float(df_today["close"].iloc[-1])
    or_range = or_high - or_low

    # Minimum OR range guard — very tight ORs produce noisy signals
    if or_range < or_mid * 0.002:  # less than 0.2% of price
        return None

    extension_threshold = or_mid * extension_threshold_pct

    # ── Short mean-reversion: price broke ABOVE OR, fade it ───────────────
    # Only if RSI is overbought (>70) confirming overextension
    above_extension = current > (or_high + extension_threshold)
    if above_extension and rsi_current > 68:
        stop   = current + atr        # Stop above current price + 1 ATR
        target = or_mid               # Target = revert to OR midpoint
        risk   = stop - current
        if risk <= 0:
            return None
        reward = current - target
        if reward / risk < 1.0:       # Require at least 1:1 R:R for fading
            return None
        return {
            "direction":   "SHORT",   # Fade the upside breakout
            "entry":       current,
            "stop":        round(stop, 2),
            "target":      round(target, 2),
            "risk":        round(risk, 2),
            "rr_ratio":    round(reward / risk, 2),
            "reason":      f"Mean-reversion SHORT | RSI={rsi_current:.0f} | "
                           f"Extended {((current - or_high)/or_mid*100):.2f}% above OR",
            "regime":      regime.get("regime"),
            "confidence":  regime.get("confidence"),
        }

    # ── Long mean-reversion: price broke BELOW OR, fade it ────────────────
    # Only if RSI is oversold (<32) confirming overextension
    below_extension = current < (or_low - extension_threshold)
    if below_extension and rsi_current < 32:
        stop   = current - atr        # Stop below current price - 1 ATR
        target = or_mid               # Target = revert to OR midpoint
        risk   = current - stop
        if risk <= 0:
            return None
        reward = target - current
        if reward / risk < 1.0:
            return None
        return {
            "direction":   "LONG",    # Fade the downside breakdown
            "entry":       current,
            "stop":        round(stop, 2),
            "target":      round(target, 2),
            "risk":        round(risk, 2),
            "rr_ratio":    round(reward / risk, 2),
            "reason":      f"Mean-reversion LONG | RSI={rsi_current:.0f} | "
                           f"Extended {((or_low - current)/or_mid*100):.2f}% below OR",
            "regime":      regime.get("regime"),
            "confidence":  regime.get("confidence"),
        }

    return None  # No valid mean-reversion setup


def compute_rsi(series: pd.Series, period: int = 14) -> float:
    """Compute current RSI value from a price series."""
    if len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.fillna(50).iloc[-1])


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Compute current ATR value."""
    if len(df) < period:
        return float((df["high"] - df["low"]).mean())
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])