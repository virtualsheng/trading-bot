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

Performance fix: Ollama is pre-warmed at startup, and per-call timeout
is reduced to 15s so a slow/unresponsive Ollama doesn't block trade execution.
"""

import json
import time
import requests
from typing import Optional

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_TAGS  = "http://localhost:11434/api/tags"
OLLAMA_MODEL = "qwen3:8b"
TIMEOUT      = 15    # Reduced from 30 — don't block trades on slow Ollama

# Module-level flag: set to False if Ollama is confirmed unreachable
_ollama_available = None   # None = not yet checked


def check_ollama_available() -> bool:
    """
    Check if Ollama is running and the model is loaded.
    Call this once at strategy startup (before_market_opens).
    Warms up the model so the first real call is fast.
    """
    global _ollama_available
    try:
        # Check Ollama is running
        resp = requests.get(OLLAMA_TAGS, timeout=5)
        if resp.status_code != 200:
            print(f"[ai_engine] Ollama not reachable (HTTP {resp.status_code})")
            _ollama_available = False
            return False

        # Check the model exists
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        model_base = OLLAMA_MODEL.split(":")[0]
        has_model  = any(model_base in m for m in models)

        if not has_model:
            print(
                f"[ai_engine] Model '{OLLAMA_MODEL}' not found in Ollama. "
                f"Run: ollama pull {OLLAMA_MODEL}"
            )
            _ollama_available = False
            return False

        # Warm up — send a trivial prompt so model is loaded into memory
        print(f"[ai_engine] Warming up Ollama ({OLLAMA_MODEL})...")
        warmup_resp = requests.post(
            OLLAMA_URL,
            json={
                "model":  OLLAMA_MODEL,
                "prompt": "Say OK.",
                "stream": False,
                "options": {"num_predict": 5, "temperature": 0},
            },
            timeout=60,   # First load can take up to 60s
        )
        if warmup_resp.status_code == 200:
            print(f"[ai_engine] Ollama ready — model loaded")
            _ollama_available = True
            return True
        else:
            print(f"[ai_engine] Ollama warmup failed (HTTP {warmup_resp.status_code})")
            _ollama_available = False
            return False

    except requests.exceptions.ConnectionError:
        print(
            "[ai_engine] Ollama not running. Start it with: ollama serve\n"
            "           AI grading will use fallback defaults."
        )
        _ollama_available = False
        return False
    except Exception as e:
        print(f"[ai_engine] Ollama check failed: {e}")
        _ollama_available = False
        return False


def _call_ollama(prompt: str, model: str = OLLAMA_MODEL) -> Optional[str]:
    """
    Raw call to Ollama.
    Skips immediately if Ollama was confirmed unavailable at startup.
    Returns response text or None on failure.
    """
    global _ollama_available

    # If we already know Ollama is down, don't waste time trying
    if _ollama_available is False:
        return None

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model":  model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 400,
                }
            },
            timeout=TIMEOUT
        )
        if resp.status_code == 200:
            _ollama_available = True   # Confirmed working
            return resp.json().get("response", "").strip()
        return None
    except requests.exceptions.Timeout:
        print(f"[ai_engine] Ollama timed out after {TIMEOUT}s — using fallback")
        return None
    except Exception:
        return None


def _parse_json_from_response(text: str) -> dict:
    """Extract JSON from LLM response even if wrapped in markdown."""
    if not text:
        return {}
    text = text.strip()

    # Strip <think>...</think> blocks (some models emit these)
    if "<think>" in text:
        end = text.find("</think>")
        if end != -1:
            text = text[end + 8:].strip()

    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        # Take the second part (inside fences)
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]

    # Find first { and last } to extract JSON object
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end+1]

    try:
        return json.loads(text.strip())
    except Exception:
        return {}


# ── Fallback responses ─────────────────────────────────────────────────────

_GRADE_FALLBACK = {
    "confidence":           0.60,
    "reasoning":            "AI grading unavailable — using default confidence",
    "flags":                ["ai_unavailable"],
    "volume_quality":       "unknown",
    "price_action_quality": "unknown",
    "approve":              True,
    "size_multiplier":      0.5,   # Half size when AI unavailable
}

_REGIME_FALLBACK = {
    "regime":            "unknown",
    "confidence":        0.5,
    "orb_suitability":   "moderate",
    "stop_adjustment":   1.0,
    "target_adjustment": 1.0,
    "reasoning":         "Regime detection unavailable",
}


# ── 1. Setup Grader ────────────────────────────────────────────────────────

def grade_setup(
    symbol: str,
    direction: str,
    candles: list,
    or_high: float,
    or_low: float,
    current_price: float,
    avg_volume: float,
) -> dict:
    """
    Grade an ORB breakout setup. Returns confidence 0.0-1.0 and size multiplier.

    If Ollama is unavailable, returns fallback with approve=True at 0.5x size
    so trades still execute but at reduced risk.
    """
    if not candles:
        return _GRADE_FALLBACK.copy()

    candle_str = "\n".join(
        f"  {i+1:2d}. O:{c['o']:.2f} H:{c['h']:.2f} L:{c['l']:.2f} "
        f"C:{c['c']:.2f} V:{int(c.get('v', 0))}"
        for i, c in enumerate(candles[-25:])
    )

    latest_vol   = candles[-1].get("v", 0) if candles else 0
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
OR Range:  {or_range:.2f} ({or_range/max(current_price,0.01)*100:.1f}% of price)
Current:   {current_price:.2f}
Breakout extension: {breakout_ext:.2f} ({ext_pct:.0f}% beyond OR boundary)
Volume ratio vs morning avg: {vol_ratio:.2f}x

LAST 25 FIVE-MINUTE CANDLES (oldest to newest):
{candle_str}

Return ONLY this JSON (no other text, no markdown):
{{
  "confidence": <0.0 to 1.0>,
  "reasoning": "<2-3 sentences>",
  "flags": ["<flag1>"],
  "volume_quality": "<low|normal|strong>",
  "price_action_quality": "<weak|moderate|strong>",
  "approve": <true if confidence >= 0.55>
}}

Flags: coiling, parabolic, low_volume, strong_volume, momentum, choppy, overextended, tight_setup"""

    response = _call_ollama(prompt)
    data     = _parse_json_from_response(response)

    if not data or "confidence" not in data:
        return _GRADE_FALLBACK.copy()

    confidence              = round(float(data.get("confidence", 0.60)), 3)
    data["confidence"]      = confidence
    data["approve"]         = confidence >= 0.55
    data["size_multiplier"] = _confidence_to_size(confidence)
    return data


def _confidence_to_size(confidence: float) -> float:
    if confidence >= 0.90:
        return 2.0
    elif confidence >= 0.75:
        return 1.5
    elif confidence >= 0.65:
        return 1.0
    elif confidence >= 0.55:
        return 0.5
    else:
        return 0.0


# ── 2. Regime Detector ─────────────────────────────────────────────────────

REGIME_CACHE = {}


def detect_regime(
    symbol: str,
    bars_5m:  list,
    bars_15m: list,
    bars_1h:  list,
    rsi_14:   float,
    atr_14:   float,
) -> dict:
    """Classify market regime. Returns fallback if Ollama unavailable."""

    def fmt_bars(bars: list, label: str) -> str:
        if not bars:
            return f"  {label}: (no data)"
        lines = [f"  {label}:"]
        for b in bars[-10:]:
            lines.append(
                f"    O:{b['o']:.2f} H:{b['h']:.2f} "
                f"L:{b['l']:.2f} C:{b['c']:.2f} V:{int(b.get('v',0))}"
            )
        return "\n".join(lines)

    prompt = f"""You are a quantitative analyst classifying market regime in real-time.

SYMBOL: {symbol}
RSI(14): {rsi_14:.1f}
ATR(14): {atr_14:.2f}

MULTI-TIMEFRAME DATA (oldest to newest):
{fmt_bars(bars_5m, '5-minute')}
{fmt_bars(bars_15m, '15-minute')}
{fmt_bars(bars_1h, '1-hour')}

Return ONLY this JSON (no markdown):
{{
  "regime": "<trending_up|trending_down|ranging|volatile|mean_reversion|low_liquidity>",
  "confidence": <0.0 to 1.0>,
  "orb_suitability": "<good|moderate|poor>",
  "stop_adjustment": <0.8 to 1.5>,
  "target_adjustment": <0.8 to 1.5>,
  "reasoning": "<2-3 sentences>"
}}"""

    response = _call_ollama(prompt)
    data     = _parse_json_from_response(response)

    if not data or "regime" not in data:
        return _REGIME_FALLBACK.copy()

    REGIME_CACHE[symbol] = data
    return data


def get_cached_regime(symbol: str) -> dict:
    return REGIME_CACHE.get(symbol, _REGIME_FALLBACK.copy())


# ── 3. Trade Narrator ──────────────────────────────────────────────────────

def narrate_trade(trade_record: dict) -> str:
    """Generate a plain-English journal entry for a completed trade."""
    # Sanitize for JSON serialization
    safe_record = {}
    for k, v in trade_record.items():
        try:
            json.dumps(v)
            safe_record[k] = v
        except Exception:
            safe_record[k] = str(v)

    prompt = f"""Write a 2-3 sentence trade journal entry for this completed trade.
Be analytical and specific. Mention what worked or what could be improved.

Trade:
{json.dumps(safe_record, indent=2)}

Return ONLY the journal text, no JSON, no labels."""

    response = _call_ollama(prompt)
    return response if response else "Narrative unavailable."