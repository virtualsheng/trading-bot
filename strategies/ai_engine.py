"""
strategies/ai_engine.py — AI-Powered Trade Analysis
─────────────────────────────────────────────────────
Provider priority (auto-fallback):
  1. Gemini (gemini-2.0-flash-lite) — free 1,500 req/day, frontier quality
  2. Groq  (qwen3-32b)              — free 14,400 req/day, very fast
  3. Ollama (qwen3:4b local)        — offline fallback, no API key needed

Set in .env:
  GEMINI_API_KEY=AIza...    # aistudio.google.com — free, no credit card
  GROQ_API_KEY=gsk_...      # console.groq.com   — free, no credit card

Capabilities:
  1. grade_setup()       — grades ORB breakout setup (confidence 0.0–1.0)
  2. detect_regime()     — classifies market regime from multi-timeframe bars
  3. generate_narrative()— plain-English journal entry for a completed trade
"""

import json
import os
import sys
import time

# llm_router.py lives in the same strategies/ directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_router import llm_call, llm_call_text, llm_available, llm_provider_status

MAX_RETRIES = 2


def check_ollama_available() -> bool:
    """
    Renamed: check_ai_available() — kept as check_ollama_available()
    for backward compatibility with callers in trend_filtered_orb.py.
    Returns True if ANY provider (Gemini, Groq, Ollama) is available.
    Prints provider status table at startup.
    """
    status = llm_provider_status()
    order  = status["active_order"]
    active = []

    for p in ["gemini", "groq", "ollama"]:
        s = status[p]
        if s["configured"]:
            marker = "✓" if p in order else "–"
            active.append(f"{marker} {p.upper():<8} {s['model']}")

    if not active:
        active = ["  no providers configured — all AI grading disabled"]

    print("[ai_engine] Provider status:")
    for line in active:
        print(f"  {line}")

    available = llm_available()
    if available:
        print(f"[ai_engine] Ready — priority: {' → '.join(order)}")
    else:
        print("[ai_engine] No AI providers available — fallback mode")
    return available


# ── Grade a breakout setup ────────────────────────────────────────────────────

_GRADE_FALLBACK = {
    "confidence":           0.60,
    "reasoning":            "AI unavailable — using default confidence",
    "flags":                ["ai_unavailable"],
    "approve":              True,
    "size_multiplier":      0.5,
}


def grade_setup(
    symbol:        str,
    direction:     str,
    or_range_pct:  float,
    breakout_pct:  float,
    vol_ratio:     float,
    rsi:           float,
    atr_pct:       float,
    regime:        str,
    current_price: float,
    avg_volume:    float,
) -> dict:
    """
    Grade a breakout setup. Returns dict with:
      confidence (0.0–1.0), approve (bool), size_multiplier (0.5–2.0),
      reasoning (str), flags (list)
    """
    prompt = f"""You are a quantitative trader grading an intraday ORB breakout setup.
Respond ONLY with valid JSON — no markdown, no explanation.

Setup:
  Symbol:      {symbol}
  Direction:   {direction} (BULL=long via leveraged ETF, BEAR=inverse ETF)
  OR range:    {or_range_pct:.2f}% of price
  Breakout:    +{breakout_pct:.2f}% above OR high
  Volume:      {vol_ratio:.2f}x average ({avg_volume:,.0f} avg)
  RSI(14):     {rsi:.1f}
  ATR/price:   {atr_pct:.2f}%
  Regime:      {regime}
  Price:       ${current_price:.2f}

Grade this setup and respond with exactly this JSON:
{{
  "confidence":      <float 0.0-1.0>,
  "approve":         <true|false>,
  "size_multiplier": <0.5|0.75|1.0|1.25|1.5|2.0>,
  "reasoning":       "<one sentence, specific to this setup>",
  "flags":           ["<flag1>", "<flag2>"]
}}

Flags to use when relevant: low_volume, overbought_rsi, wide_or_range,
  thin_breakout, strong_regime_alignment, volume_surge, tight_setup.
Set approve=false only for clearly weak setups (vol_ratio < 0.5, rsi > 80, 
  or_range_pct > 5%, breakout_pct < 0.05%)."""

    result = llm_call(prompt, expect_json=True, timeout=20, tag=f"grade/{symbol}")

    if result and "confidence" in result:
        return {
            "confidence":      round(float(result.get("confidence", 0.6)), 3),
            "approve":         bool(result.get("approve", True)),
            "size_multiplier": float(result.get("size_multiplier", 1.0)),
            "reasoning":       str(result.get("reasoning", "")),
            "flags":           list(result.get("flags", [])),
        }
    return _GRADE_FALLBACK.copy()


# ── Detect market regime ──────────────────────────────────────────────────────

_REGIME_FALLBACK = {
    "regime":            "unknown",
    "confidence":        0.5,
    "orb_suitability":   "moderate",
    "stop_adjustment":   1.0,
    "target_adjustment": 1.0,
    "reasoning":         "Regime detection unavailable",
}


def detect_regime(
    symbol:     str,
    bars_5m:    list[dict],
    bars_15m:   list[dict],
    bars_1h:    list[dict],
) -> dict:
    """
    Classify market regime from multi-timeframe OHLCV bars.
    bars_*: list of {open, high, low, close, volume} dicts, newest last.
    Returns regime dict with orb_suitability and stop/target adjustments.
    """
    def fmt_bars(bars: list[dict], n: int = 5) -> str:
        recent = bars[-n:]
        return " | ".join(
            f"O={b['open']:.2f} H={b['high']:.2f} L={b['low']:.2f} "
            f"C={b['close']:.2f} V={b.get('volume', 0):,.0f}"
            for b in recent
        )

    prompt = f"""Classify the current intraday market regime for {symbol}.
Respond ONLY with valid JSON — no markdown.

Recent bars (newest last):
5-min  (last 5): {fmt_bars(bars_5m,  5)}
15-min (last 5): {fmt_bars(bars_15m, 5)}
1-hour (last 3): {fmt_bars(bars_1h,  3)}

Regimes: trending_up | trending_down | ranging | mean_reversion | volatile | low_liquidity

{{
  "regime":            "<regime>",
  "confidence":        <0.0-1.0>,
  "orb_suitability":   "<good|moderate|poor>",
  "stop_adjustment":   <0.8-1.5>,
  "target_adjustment": <0.8-1.5>,
  "reasoning":         "<one sentence>"
}}

stop_adjustment > 1.0 means widen stops (volatile), < 1.0 means tighten.
orb_suitability=poor means skip ORB entries today."""

    result = llm_call(prompt, expect_json=True, timeout=20, tag=f"regime/{symbol}")

    if result and "regime" in result:
        out = {
            "regime":            str(result.get("regime",            "unknown")),
            "confidence":        round(float(result.get("confidence", 0.5)), 3),
            "orb_suitability":   str(result.get("orb_suitability",   "moderate")),
            "stop_adjustment":   float(result.get("stop_adjustment",  1.0)),
            "target_adjustment": float(result.get("target_adjustment",1.0)),
            "reasoning":         str(result.get("reasoning",          "")),
        }
        REGIME_CACHE[symbol] = out   # keep get_cached_regime() current
        return out
    return _REGIME_FALLBACK.copy()


# ── Generate trade narrative ──────────────────────────────────────────────────

def generate_narrative(
    symbol:       str,
    direction:    str,
    entry_price:  float,
    exit_price:   float,
    pnl:          float,
    pnl_pct:      float,
    exit_reason:  str,
    hold_minutes: int,
    regime:       str,
    confidence:   float,
) -> str:
    """
    Generate a 2-3 sentence plain-English journal entry for a completed trade.
    """
    prompt = f"""Write a 2-3 sentence trade journal entry for this intraday trade.
Plain prose, no bullets, no markdown. Be specific about what worked or didn't.

Trade: {direction} {symbol}
Entry: ${entry_price:.2f} → Exit: ${exit_price:.2f}
P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)
Exit reason: {exit_reason}
Hold time: {hold_minutes} minutes
Regime: {regime} | AI confidence at entry: {confidence:.0%}"""

    result = llm_call_text(prompt, timeout=15, tag=f"narrative/{symbol}")

    if result and len(result.strip()) > 20:
        return result.strip()

    direction_word = "long" if "BULL" in direction.upper() else "short (inverse ETF)"
    outcome = "profitable" if pnl >= 0 else "stopped out"
    return (
        f"Took a {direction_word} position in {symbol} at ${entry_price:.2f}. "
        f"Trade was {outcome} at ${exit_price:.2f} ({pnl:+.2f}, {pnl_pct:+.1f}%) "
        f"after {hold_minutes} minutes via {exit_reason}."
    )


# ── Backward-compatibility aliases ────────────────────────────────────────────
# trend_filtered_orb.py imports these names — keep them working.

REGIME_CACHE: dict = {}


def get_cached_regime(symbol: str) -> dict:
    """Return last cached regime for symbol, or fallback."""
    return REGIME_CACHE.get(symbol, _REGIME_FALLBACK.copy())


def narrate_trade(trade_record: dict) -> str:
    """
    Generate narrative from a trade record dict.
    Alias for generate_narrative() using dict fields.
    """
    return generate_narrative(
        symbol       = trade_record.get("symbol",       "?"),
        direction    = trade_record.get("direction",    "BULL"),
        entry_price  = float(trade_record.get("entry_price",  0)),
        exit_price   = float(trade_record.get("exit_price",   0)),
        pnl          = float(trade_record.get("pnl",          0)),
        pnl_pct      = float(trade_record.get("pnl_pct",      0)),
        exit_reason  = trade_record.get("exit_reason",  "unknown"),
        hold_minutes = int(trade_record.get("hold_minutes",   0)),
        regime       = trade_record.get("regime",       "unknown"),
        confidence   = float(trade_record.get("ai_confidence", 0.6)),
    )


def _patch_regime_cache(symbol: str, data: dict):
    """Called by detect_regime() to keep REGIME_CACHE updated."""
    REGIME_CACHE[symbol] = data