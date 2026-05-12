"""
ai_engine.py — AI-Powered Trade Analysis via Ollama
────────────────────────────────────────────────────
Provides three capabilities:
  1. setup_grader:     Grades a breakout setup from OHLC candles (0.0–1.0 confidence)
  2. regime_detector:  Classifies market regime from multi-timeframe data
  3. trade_narrator:   Generates a plain-English explanation of a trade for journaling

Uses Ollama running locally — no API key needed, no cost.
Default model: qwen3:8b (fast, good reasoning)
Fallback: returns neutral scores if Ollama is unreachable.
"""

import json
import requests
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:8b"   # Change to llama3.2:3b for faster/lighter
TIMEOUT      = 30            # Seconds before giving up on Ollama


def _call_ollama(prompt: str, model: str = OLLAMA_MODEL) -> Optional[str]:
    """Raw call to Ollama. Returns response text or None on failure."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model":  model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,   # Low temp = consistent, analytical output
                    "num_predict": 300,
                }
            },
            timeout=TIMEOUT
        )
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
        return None
    except Exception:
        return None


def _parse_json_from_response(text: str) -> dict:
    """Extract JSON from LLM response even if wrapped in markdown."""
    if not text:
        return {}
    # Strip markdown code fences if present
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text.strip())
    except Exception:
        return {}


# ── 1. Setup Grader ───────────────────────────────────────────────────────

def grade_setup(
    symbol: str,
    direction: str,           # "LONG" or "SHORT"
    candles: list[dict],      # Last 20-30 candles: [{o,h,l,c,v}, ...]
    or_high: float,
    or_low: float,
    current_price: float,
    avg_volume: float,
) -> dict:
    """
    Ask the LLM to grade the quality of an ORB breakout setup.

    Returns:
        {
            "confidence": 0.0-1.0,
            "size_multiplier": 0.0-2.0,
            "reasoning": "plain english explanation",
            "approve": True/False,
            "flags": ["coiling", "parabolic", "low_volume", ...]
        }
    """
    # Format candles as compact string for prompt
    candle_str = "\n".join(
        f"  {i+1:2d}. O:{c['o']:.2f} H:{c['h']:.2f} L:{c['l']:.2f} "
        f"C:{c['c']:.2f} V:{int(c['v'])}"
        for i, c in enumerate(candles[-25:])  # Last 25 candles max
    )

    latest_vol   = candles[-1]["v"] if candles else 0
    vol_ratio    = latest_vol / avg_volume if avg_volume > 0 else 1.0
    or_range     = or_high - or_low
    breakout_ext = abs(current_price - (or_high if direction == "LONG" else or_low))
    ext_pct      = (breakout_ext / or_range * 100) if or_range > 0 else 0

    prompt = f"""You are an expert intraday trader analyzing an Opening Range Breakout (ORB) setup.

TRADE SETUP:
Symbol:    {symbol}
Direction: {direction}
OR High:   {or_high:.2f}
OR Low:    {or_low:.2f}
OR Range:  {or_range:.2f} ({or_range/current_price*100:.1f}% of price)
Current:   {current_price:.2f}
Breakout extension: {breakout_ext:.2f} ({ext_pct:.0f}% beyond OR boundary)
Volume ratio vs morning avg: {vol_ratio:.2f}x

LAST 25 FIVE-MINUTE CANDLES (oldest to newest):
{candle_str}

ANALYSIS TASK:
Evaluate this ORB breakout quality and return ONLY a JSON object with no other text.

Consider:
1. Is volume INCREASING on the breakout (ratio > 1.2 is good)?
2. Is price "coiling" tightly before breakout (narrow range = good)?
3. Is the breakout overextended / parabolic (>50% beyond OR = risky)?
4. Is the candle body strong (close near high for longs, low for shorts)?
5. Is there clear momentum (successive bars in direction)?

Return this exact JSON structure:
{{
  "confidence": <0.0 to 1.0>,
  "reasoning": "<2-3 sentence explanation>",
  "flags": ["<flag1>", "<flag2>"],
  "volume_quality": "<low|normal|strong>",
  "price_action_quality": "<weak|moderate|strong>",
  "approve": <true if confidence >= 0.55 else false>
}}

Flags can include: "coiling", "parabolic", "low_volume", "strong_volume",
"momentum", "choppy", "wide_spread", "tight_setup", "overextended"
"""

    response = _call_ollama(prompt)
    data     = _parse_json_from_response(response)

    if not data or "confidence" not in data:
        # Ollama unreachable or bad response — return neutral approval
        return {
            "confidence":     0.60,
            "reasoning":      "AI grading unavailable — using default confidence",
            "flags":          ["ai_unavailable"],
            "volume_quality": "unknown",
            "price_action_quality": "unknown",
            "approve":        True,
        }

    confidence = float(data.get("confidence", 0.60))
    data["confidence"]       = round(confidence, 3)
    data["approve"]          = confidence >= 0.55
    data["size_multiplier"]  = _confidence_to_size(confidence)
    return data


def _confidence_to_size(confidence: float) -> float:
    """
    Map confidence score to position size multiplier.
    Elite: 2x, Good: 1x, Weak: 0.5x, Below threshold: 0x
    """
    if confidence >= 0.90:
        return 2.0    # Elite setup
    elif confidence >= 0.75:
        return 1.5    # Strong setup
    elif confidence >= 0.65:
        return 1.0    # Normal setup
    elif confidence >= 0.55:
        return 0.5    # Weak setup — half size
    else:
        return 0.0    # Skip trade


# ── 2. Regime Detector ────────────────────────────────────────────────────

REGIME_CACHE = {}   # {symbol: {regime, timestamp, ...}}


def detect_regime(
    symbol: str,
    bars_5m:  list[dict],   # Last 20 x 5-minute bars
    bars_15m: list[dict],   # Last 20 x 15-minute bars
    bars_1h:  list[dict],   # Last 10 x 1-hour bars
    rsi_14:   float,
    atr_14:   float,
) -> dict:
    """
    Classify the current market regime using multi-timeframe data.

    Returns:
        {
            "regime": "trending_up" | "trending_down" | "ranging" |
                      "volatile" | "mean_reversion" | "low_liquidity",
            "confidence": 0.0-1.0,
            "orb_suitability": "good" | "moderate" | "poor",
            "stop_adjustment": 1.0,   # Multiplier for stop distance
            "target_adjustment": 1.0, # Multiplier for target distance
            "reasoning": "..."
        }
    """
    def fmt_bars(bars: list[dict], label: str) -> str:
        lines = [f"  {label}:"]
        for b in bars[-10:]:
            lines.append(
                f"    O:{b['o']:.2f} H:{b['h']:.2f} "
                f"L:{b['l']:.2f} C:{b['c']:.2f} V:{int(b['v'])}"
            )
        return "\n".join(lines)

    prompt = f"""You are a quantitative analyst performing real-time market regime classification.

SYMBOL: {symbol}
RSI(14): {rsi_14:.1f}
ATR(14): {atr_14:.2f}

MULTI-TIMEFRAME PRICE DATA (recent bars, oldest to newest):
{fmt_bars(bars_5m, '5-minute')}
{fmt_bars(bars_15m, '15-minute')}
{fmt_bars(bars_1h, '1-hour')}

CLASSIFICATION TASK:
Identify the current market regime and return ONLY a JSON object.

Regime definitions:
- trending_up:      Clear upward momentum across timeframes, higher highs/lows
- trending_down:    Clear downward momentum, lower highs/lows
- ranging:          Price oscillating in a band, no clear direction
- volatile:         Large ATR, erratic moves, hard to predict direction
- mean_reversion:   Price extended from MA, likely to snap back
- low_liquidity:    Thin volume, wide spreads, avoid trading

ORB strategy works BEST in: trending_up, trending_down
ORB strategy works POORLY in: ranging, mean_reversion, low_liquidity

Return this exact JSON:
{{
  "regime": "<one of the six regimes above>",
  "confidence": <0.0 to 1.0>,
  "orb_suitability": "<good|moderate|poor>",
  "stop_adjustment": <0.8 to 1.5>,
  "target_adjustment": <0.8 to 1.5>,
  "reasoning": "<2-3 sentence explanation>"
}}

stop_adjustment > 1.0 = widen stops (use in volatile/ranging markets)
target_adjustment < 1.0 = tighten targets (use in mean reversion)
"""

    response = _call_ollama(prompt)
    data     = _parse_json_from_response(response)

    if not data or "regime" not in data:
        return {
            "regime":            "unknown",
            "confidence":        0.5,
            "orb_suitability":   "moderate",
            "stop_adjustment":   1.0,
            "target_adjustment": 1.0,
            "reasoning":         "Regime detection unavailable",
        }

    # Cache result
    REGIME_CACHE[symbol] = data
    return data


def get_cached_regime(symbol: str) -> dict:
    """Return last known regime for a symbol, or neutral default."""
    return REGIME_CACHE.get(symbol, {
        "regime":            "unknown",
        "orb_suitability":   "moderate",
        "stop_adjustment":   1.0,
        "target_adjustment": 1.0,
    })


# ── 3. Trade Narrator ─────────────────────────────────────────────────────

def narrate_trade(trade_record: dict) -> str:
    """
    Generate a plain-English journal entry for a completed trade.
    Used to populate the 'ai_narrative' field in the trade journal.
    """
    prompt = f"""Write a 2-3 sentence trade journal entry for this completed trade.
Be analytical and specific. Mention what worked or what could be improved.

Trade data:
{json.dumps(trade_record, indent=2)}

Return ONLY the journal text, no JSON, no labels.
"""
    response = _call_ollama(prompt)
    return response if response else "Narrative generation unavailable."