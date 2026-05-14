"""
ai_engine.py — AI-Powered Trade Analysis via Ollama
────────────────────────────────────────────────────
Provides three capabilities:
  1. setup_grader:     Grades a breakout setup from OHLC candles (0.0–1.0 confidence)
  2. regime_detector:  Classifies market regime from multi-timeframe data
  3. trade_narrator:   Generates a plain-English journal entry for a trade

Uses Ollama running locally — no API key needed, no cost.
Default model: llama3.2:3b (fast, low RAM — swap for qwen3:8b for better reasoning)
Fallback: returns neutral scores if Ollama is unreachable.

Reliability fixes (v2):
  - check_ollama_available() warms up with a realistic regime-style prompt
    so the model KV cache is primed before the first detect_regime() call.
  - _call_ollama() retries up to MAX_RETRIES times with exponential backoff
    (2s → 4s) before giving up and using fallback defaults.
  - detect_regime() uses timeout=30 (longer than per-trade grade_setup).
  - TIMEOUT raised to 25s (from 15s) to reduce first-attempt timeouts on
    machines running llama3.2:3b or qwen3:8b.

Reliability fixes (v3):
  - check_ollama_available() warms up with a realistic regime-style prompt
    so the model KV cache is primed before the first detect_regime() call.
  - _call_ollama() retries up to MAX_RETRIES times with exponential backoff.
  - TIMEOUT raised to 25s (from 15s).
  - detect_regime() uses fmt_bars([-5:]) instead of [-10:] — smaller prompt
    generates faster, eliminating the 30s timeout on llama3.2:3b.
    Trend direction is readable from 5 bars per timeframe.
"""

import json
import time
import requests
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_TAGS  = "http://localhost:11434/api/tags"
OLLAMA_MODEL = "llama3.2:3b"

# Per-trade grading timeout. 25s eliminates most first-attempt timeouts
# on machines where generation takes 18–22s.
# detect_regime() overrides this with timeout=30 since it runs at open.
TIMEOUT     = 25

# Retry on timeout: 1 + MAX_RETRIES total attempts, backoff 2s → 4s.
MAX_RETRIES = 2

# Set False if Ollama is confirmed unreachable at startup.
_ollama_available = None


def check_ollama_available() -> bool:
    """
    Check if Ollama is running and the model is loaded.
    Warms up with a regime-shaped prompt to prime the KV cache before
    the first real detect_regime() call fires at market open.
    """
    global _ollama_available
    try:
        resp = requests.get(OLLAMA_TAGS, timeout=5)
        if resp.status_code != 200:
            print(f"[ai_engine] Ollama not reachable (HTTP {resp.status_code})")
            _ollama_available = False
            return False

        models     = [m.get("name", "") for m in resp.json().get("models", [])]
        model_base = OLLAMA_MODEL.split(":")[0]
        if not any(model_base in m for m in models):
            print(
                f"[ai_engine] Model '{OLLAMA_MODEL}' not found. "
                f"Run: ollama pull {OLLAMA_MODEL}"
            )
            _ollama_available = False
            return False

        print(f"[ai_engine] Warming up Ollama ({OLLAMA_MODEL})...")
        warmup_prompt = (
            "You are a quantitative analyst. Given this market data for QQQ: "
            "5m bars show higher highs and higher lows, RSI=55, ATR=1.2. "
            "Classify the regime as one of: trending_up, trending_down, "
            "ranging, mean_reversion, volatile, low_liquidity. "
            'Reply with ONLY this JSON: {"regime":"trending_up","confidence":0.8,'
            '"orb_suitability":"good","stop_adjustment":1.0,'
            '"target_adjustment":1.0,"reasoning":"Warmup probe."}'
        )
        warmup_resp = requests.post(
            OLLAMA_URL,
            json={
                "model":  OLLAMA_MODEL,
                "prompt": warmup_prompt,
                "stream": False,
                "options": {"num_predict": 80, "temperature": 0},
            },
            timeout=60,
        )
        if warmup_resp.status_code == 200:
            print("[ai_engine] Ollama ready — model loaded and warmed")
            _ollama_available = True
            return True
        print(f"[ai_engine] Ollama warmup failed (HTTP {warmup_resp.status_code})")
        _ollama_available = False
        return False

    except requests.exceptions.ConnectionError:
        print("[ai_engine] Ollama not running — start with: ollama serve")
        _ollama_available = False
        return False
    except Exception as e:
        print(f"[ai_engine] Ollama check failed: {e}")
        _ollama_available = False
        return False


def _call_ollama(prompt: str, model: str = OLLAMA_MODEL,
                 timeout: int = None) -> Optional[str]:
    """Raw Ollama call with retry on timeout."""
    global _ollama_available
    if _ollama_available is False:
        return None

    t = timeout if timeout is not None else TIMEOUT

    for attempt in range(1 + MAX_RETRIES):
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={
                    "model":  model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 400},
                },
                timeout=t,
            )
            if resp.status_code == 200:
                _ollama_available = True
                if attempt > 0:
                    print(f"[ai_engine] Ollama succeeded on retry {attempt}")
                return resp.json().get("response", "").strip()
            return None

        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES:
                wait = 2 ** (attempt + 1)
                print(
                    f"[ai_engine] Ollama timed out after {t}s "
                    f"(attempt {attempt+1}/{1+MAX_RETRIES}) — retrying in {wait}s..."
                )
                time.sleep(wait)
            else:
                print(
                    f"[ai_engine] Ollama timed out after {t}s — "
                    f"all {1+MAX_RETRIES} attempts exhausted, using fallback"
                )
        except Exception as e:
            print(f"[ai_engine] Ollama call error: {e}")
            return None

    return None


def _parse_json_from_response(text: str) -> dict:
    """Extract JSON from LLM response, tolerating markdown fences and <think> blocks."""
    if not text:
        return {}
    text = text.strip()
    if "<think>" in text:
        end = text.find("</think>")
        if end != -1:
            text = text[end + 8:].strip()
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end+1]
    try:
        return json.loads(text.strip())
    except Exception:
        return {}


# ── Fallbacks ──────────────────────────────────────────────────────────────

_GRADE_FALLBACK = {
    "confidence":           0.60,
    "reasoning":            "AI grading unavailable — using default confidence",
    "flags":                ["ai_unavailable"],
    "volume_quality":       "unknown",
    "price_action_quality": "unknown",
    "approve":              True,
    "size_multiplier":      0.5,
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
    """Grade an ORB breakout setup. Returns confidence 0.0–1.0 + size multiplier."""
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
    if confidence >= 0.90: return 2.0
    if confidence >= 0.75: return 1.5
    if confidence >= 0.65: return 1.0
    if confidence >= 0.55: return 0.5
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
    """
    Classify market regime from multi-timeframe bar data.

    Uses only the last 5 bars per timeframe in the prompt (down from 10).
    Smaller prompt = faster generation on llama3.2:3b, eliminating 30s timeouts.
    Five bars is sufficient for trend direction — we don't need 10+ bars for
    a simple regime classification.
    """
    def fmt_bars(bars: list, label: str) -> str:
        if not bars:
            return f"  {label}: (no data)"
        lines = [f"  {label}:"]
        # ← 5 bars instead of 10 — keeps prompt small and fast
        for b in bars[-5:]:
            lines.append(
                f"    O:{b['o']:.2f} H:{b['h']:.2f} "
                f"L:{b['l']:.2f} C:{b['c']:.2f} V:{int(b.get('v', 0))}"
            )
        return "\n".join(lines)

    prompt = f"""You are a quantitative analyst classifying market regime in real-time.

SYMBOL: {symbol}
RSI(14): {rsi_14:.1f}
ATR(14): {atr_14:.2f}

RECENT MULTI-TIMEFRAME DATA (5 bars each, oldest to newest):
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
  "reasoning": "<1-2 sentences>"
}}"""

    # Regime calls get 30s timeout (vs 25s for per-trade grading).
    # Runs at market open and every 30 min — latency here is acceptable.
    response = _call_ollama(prompt, timeout=30)
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