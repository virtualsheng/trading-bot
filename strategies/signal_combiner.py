"""
signal_combiner.py — Fusion layer for technical + sentiment signals
────────────────────────────────────────────────────────────────────
Combines output from:
  • signal_engine.get_technical_signal()  (this bot's technical analysis)
  • Jeff's Sentiment Trading Alpha bot    (geopolitical/news sentiment)
    https://github.com/techjeffe/Sentiment-Trading-Alpha

STATUS: Technical signal integration is live. Sentiment signal integration
        is a stub — wire up adapt_sentiment_signal() once Jeff's output
        schema is confirmed.

Signal schemas
──────────────
Technical signal (from signal_engine.py):
  {
    "action":       "BUY" | "SELL" | "STRONG_BUY" | "STRONG_SELL" | "HOLD" | "ERROR",
    "bull_score":   int (0–5),
    "bear_score":   int (0–5),
    "rsi":          float,
    "volume_ratio": float,
    "above_sma200": bool,
    ...
  }

Sentiment signal (Jeff's bot — adapt as needed in adapt_sentiment_signal()):
  Expected output after adaptation:
  {
    "action":     "BUY" | "SELL" | "HOLD",
    "confidence": int (0–100),
    "source":     str,   # e.g. "geopolitical_rss", "earnings_news"
  }

Combined output:
  {
    "action":             "BUY" | "SELL" | "HOLD",
    "confidence":         int (0–100),
    "agreement":          "CONFIRMED" | "TECHNICAL_ONLY" | "SENTIMENT_ONLY" | "CONFLICT",
    "execute":            bool,
    "technical_action":   str,
    "technical_strength": "STRONG" | "MODERATE" | "WEAK",
    "sentiment_action":   str,
    "sentiment_source":   str,
    "reason":             str,
  }
"""

from __future__ import annotations


# ── Sentiment adapter ──────────────────────────────────────────────────────────

def adapt_sentiment_signal(raw_sentiment: dict) -> dict:
    """
    Normalize Jeff's Sentiment Trading Alpha output into the schema
    this combiner expects. Update this function once his output format
    is confirmed.

    Expected Jeff output (based on repo structure — verify and update):
      {
        "ticker": "QQQ",
        "signal": "bullish" | "bearish" | "neutral",
        "score":  float (0.0–1.0),
        "source": str,
      }
    """
    # TODO: Update field names once Jeff's output schema is confirmed.
    # Current mapping is a reasonable guess based on his repo structure.
    raw_action = raw_sentiment.get("signal", raw_sentiment.get("action", "neutral"))
    raw_score  = raw_sentiment.get("score",  raw_sentiment.get("confidence", 0))

    # Normalize action
    action_map = {
        "bullish": "BUY",
        "bearish": "SELL",
        "neutral": "HOLD",
        "buy":     "BUY",
        "sell":    "SELL",
        "hold":    "HOLD",
    }
    action = action_map.get(str(raw_action).lower(), "HOLD")

    # Normalize score → 0–100 int
    if isinstance(raw_score, float) and raw_score <= 1.0:
        confidence = int(raw_score * 100)
    else:
        confidence = int(raw_score)

    return {
        "action":     action,
        "confidence": confidence,
        "source":     raw_sentiment.get("source", "sentiment_alpha"),
    }


# ── Technical signal normalizer ────────────────────────────────────────────────

def _technical_strength(signal: dict) -> str:
    """
    Derive a STRONG / MODERATE / WEAK label from signal_engine output.
    signal_engine doesn't return a 'strength' key — we derive it from
    bull_score/bear_score and volume_ratio.
    """
    action     = signal.get("action", "HOLD")
    vol_ratio  = signal.get("volume_ratio", 1.0) or 1.0

    if "STRONG" in action:
        raw_strength = "STRONG"
    elif action in ("BUY", "SELL"):
        bull = signal.get("bull_score", 0) or 0
        bear = signal.get("bear_score", 0) or 0
        score = bull if action == "BUY" else bear
        raw_strength = "MODERATE" if score >= 4 else "WEAK"
    else:
        return "WEAK"

    # Volume confirmation upgrades MODERATE → STRONG
    if raw_strength == "MODERATE" and vol_ratio >= 1.3:
        return "STRONG"
    return raw_strength


def _normalize_technical_action(action: str) -> str:
    """Map STRONG_BUY / STRONG_SELL → BUY / SELL for direction comparison."""
    return action.replace("STRONG_", "")


# ── Core combiner ──────────────────────────────────────────────────────────────

def combine_signals(
    technical_signal: dict,
    sentiment_signal: dict | None = None,
    ai_min_confidence: float = 0.55,
) -> dict:
    """
    Combine a technical signal with an optional sentiment signal.

    Args:
        technical_signal:   Output from signal_engine.get_technical_signal()
        sentiment_signal:   Raw output from Jeff's bot (pre-adaptation), or
                            already-adapted dict, or None if not available.
        ai_min_confidence:  Minimum confidence (0.0–1.0) to set execute=True.
                            Should match TrendFilteredORB's ai_min_confidence param.

    Returns:
        Combined signal dict (see module docstring for schema).
    """
    t_action_raw = technical_signal.get("action", "HOLD")
    t_action     = _normalize_technical_action(t_action_raw)
    t_strength   = _technical_strength(technical_signal)

    # Base confidence from technical signal alone (0–100)
    strength_confidence = {"STRONG": 80, "MODERATE": 65, "WEAK": 40}
    t_confidence = strength_confidence[t_strength]

    # ── Sentiment available ────────────────────────────────────────────────────
    if sentiment_signal is not None:
        # Accept either raw (Jeff's format) or pre-adapted
        if "confidence" not in sentiment_signal or "action" not in sentiment_signal:
            adapted = adapt_sentiment_signal(sentiment_signal)
        else:
            adapted = sentiment_signal

        s_action     = adapted.get("action", "HOLD")
        s_confidence = adapted.get("confidence", 0)
        s_source     = adapted.get("source", "sentiment_alpha")

        boost_map = {"STRONG": 20, "MODERATE": 10, "WEAK": 0}
        boost     = boost_map[t_strength]

        if t_action == s_action and t_action != "HOLD":
            combined_confidence = min(s_confidence + boost, 99)
            combined_action     = t_action
            agreement           = "CONFIRMED"
            reason = (
                f"Both signals agree: technical={t_action_raw} ({t_strength}), "
                f"sentiment={s_action} ({s_confidence}% confidence)"
            )
        elif t_action == "HOLD" and s_action != "HOLD":
            combined_confidence = s_confidence
            combined_action     = s_action
            agreement           = "SENTIMENT_ONLY"
            reason = f"Sentiment leads ({s_action} {s_confidence}%), technicals neutral"
        elif s_action == "HOLD" and t_action != "HOLD":
            combined_confidence = t_confidence
            combined_action     = t_action
            agreement           = "TECHNICAL_ONLY"
            reason = f"Technicals lead ({t_action_raw} {t_strength}), sentiment neutral"
        else:
            # Directional conflict → stand down
            combined_confidence = 0
            combined_action     = "HOLD"
            agreement           = "CONFLICT"
            reason = f"Signal conflict: technical={t_action}, sentiment={s_action} — skipping"

    # ── Technical only (no sentiment) ─────────────────────────────────────────
    else:
        s_action     = "HOLD"
        s_source     = "none"
        combined_action     = t_action
        combined_confidence = t_confidence
        agreement           = "TECHNICAL_ONLY"
        reason = f"No sentiment signal — technical only: {t_action_raw} ({t_strength})"

    # Convert confidence to 0.0–1.0 for comparison against ai_min_confidence
    confidence_normalized = combined_confidence / 100.0

    execute = (
        combined_action != "HOLD"
        and agreement in ("CONFIRMED", "TECHNICAL_ONLY")
        and confidence_normalized >= ai_min_confidence
    )

    return {
        "action":             combined_action,
        "confidence":         combined_confidence,           # 0–100 int
        "confidence_float":   round(confidence_normalized, 3),  # 0.0–1.0
        "agreement":          agreement,
        "execute":            execute,
        "technical_action":   t_action_raw,
        "technical_strength": t_strength,
        "sentiment_action":   s_action,
        "sentiment_source":   s_source,
        "reason":             reason,
    }


# ── Quick test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Simulate signal_engine output
    tech = {
        "action":       "STRONG_BUY",
        "bull_score":   5,
        "bear_score":   0,
        "rsi":          38.5,
        "volume_ratio": 1.45,
        "above_sma200": True,
    }

    # Simulate Jeff's sentiment output
    sentiment_raw = {
        "ticker": "QQQ",
        "signal": "bullish",
        "score":  0.82,
        "source": "geopolitical_rss",
    }

    result = combine_signals(tech, sentiment_raw)
    print("Combined signal:")
    for k, v in result.items():
        print(f"  {k:<22}: {v}")

    print()
    result_no_sentiment = combine_signals(tech)
    print("Technical-only signal:")
    for k, v in result_no_sentiment.items():
        print(f"  {k:<22}: {v}")