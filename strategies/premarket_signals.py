"""
premarket_signals.py — Pre-market enrichment layer
────────────────────────────────────────────────────
Three signal sources that run at startup / before_market_opens():

  1. Gap analysis        — pre-market price vs prior close via Alpaca
  2. Alpaca News         — headline sentiment via Alpaca News API (free, same key)
  3. Sentiment-Trading-Alpha — macro directional signal from STA REST API

Sentiment-Trading-Alpha API (confirmed from source code):
  Endpoint:  POST /api/v1/analyze
  Auth:      X-Admin-Token header (SENTIMENT_ADMIN_TOKEN env var)

  Request body:
    symbols:          ["SPY", "QQQ"]  ← ONLY these two, always
    max_posts:        20
    include_backtest: false
    lookback_days:    14

Why only SPY and QQQ:
  STA's validator requires every symbol to be in its registered
  DEFAULT_TRACKED_SYMBOLS = ["USO", "IBIT", "QQQ", "SPY"] or its
  custom_symbols config. Any other symbol (NANR, GDE, AMAT etc.)
  causes a 400 Bad Request. Rather than fight this, we use STA for
  what it does best: macro market direction. The portfolio-level
  signal (LONG/SHORT/HOLD) from SPY+QQQ analysis is applied as a
  conviction modifier to all our symbols. This is correct usage —
  STA is a macro sentiment engine, not a per-stock screener.
"""

from __future__ import annotations

import os
import threading
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

ALPACA_BASE      = "https://data.alpaca.markets"
ALPACA_NEWS_BASE = "https://data.alpaca.markets/v1beta1"

SENTIMENT_BASE  = os.getenv("SENTIMENT_API_URL",    "http://localhost:8000")
SENTIMENT_TOKEN = os.getenv("SENTIMENT_ADMIN_TOKEN", "")

# Confirmed from Sentiment-Trading-Alpha source:
#   backend/main.py:             app.include_router(analysis_router, prefix="/api/v1")
#   backend/routers/analysis.py: router.post("/analyze")
SENTIMENT_ANALYZE_URL = f"{SENTIMENT_BASE}/api/v1/analyze"

# Only send STA's built-in default symbols — anything else causes a 400.
# DEFAULT_TRACKED_SYMBOLS in STA source = ["USO", "IBIT", "QQQ", "SPY"]
# We use SPY+QQQ for broad market direction.
STA_SYMBOLS = ["SPY", "QQQ"]

# Cache TTL — re-fetch if older than this
SENTIMENT_MAX_AGE_MINUTES = 90

_sentiment_cache:      dict               = {}
_sentiment_cache_time: Optional[datetime] = None
_sentiment_lock = threading.Lock()


# ── 1. Pre-market gap analysis ─────────────────────────────────────────────────

def get_premarket_gaps(symbols: list[str], api_key: str, api_secret: str) -> dict:
    """Fetch pre-market price vs prior close for each symbol via Alpaca Data API."""
    results = {}
    headers = {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }

    for symbol in symbols:
        try:
            resp = requests.get(
                f"{ALPACA_BASE}/v2/stocks/{symbol}/bars",
                params={"timeframe": "1Day", "limit": 2, "adjustment": "raw"},
                headers=headers,
                timeout=10,
            )
            bars = resp.json().get("bars", [])
            if len(bars) < 1:
                results[symbol] = {"gap_signal": "FLAT", "gap_pct": 0.0}
                continue
            prior_close = float(bars[-1]["c"])

            quote_resp = requests.get(
                f"{ALPACA_BASE}/v2/stocks/{symbol}/quotes/latest",
                headers=headers,
                timeout=10,
            )
            quote     = quote_resp.json().get("quote", {})
            pre_price = float(quote.get("ap", 0) or quote.get("bp", 0) or prior_close)
            gap_pct   = ((pre_price - prior_close) / prior_close * 100) if prior_close else 0.0

            if gap_pct >= 0.5:
                gap_signal = "GAP_UP"
            elif gap_pct <= -0.5:
                gap_signal = "GAP_DOWN"
            else:
                gap_signal = "FLAT"

            results[symbol] = {
                "gap_pct":       round(gap_pct, 3),
                "gap_signal":    gap_signal,
                "gap_vol_ratio": 1.0,
            }
        except Exception:
            results[symbol] = {"gap_signal": "FLAT", "gap_pct": 0.0}

    return results


# ── 2. Alpaca News Sentiment ───────────────────────────────────────────────────

_POSITIVE_WORDS = {
    "beat", "beats", "surge", "surges", "rally", "rallies", "gain", "gains",
    "rise", "rises", "upgrade", "upgraded", "outperform", "strong", "record",
    "growth", "profit", "revenue", "buyback", "dividend", "positive", "bullish",
    "above", "exceed", "exceeds", "raised", "raises", "breakthrough", "win",
}
_NEGATIVE_WORDS = {
    "miss", "misses", "plunge", "plunges", "fall", "falls", "drop", "drops",
    "decline", "declines", "downgrade", "downgraded", "underperform", "weak",
    "loss", "losses", "cut", "cuts", "below", "concern", "concerns", "risk",
    "lawsuit", "probe", "investigation", "recall", "warning", "bearish", "sell",
}


def get_alpaca_news_sentiment(symbols: list[str], api_key: str, api_secret: str) -> dict:
    """Score headlines from Alpaca News for each symbol."""
    results = {}
    headers = {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for symbol in symbols:
        try:
            resp = requests.get(
                f"{ALPACA_NEWS_BASE}/news",
                params={"symbols": symbol, "start": since, "limit": 10},
                headers=headers,
                timeout=10,
            )
            news = resp.json().get("news", [])
            if not news:
                results[symbol] = {"news_sentiment": 0.0, "news_headline_count": 0}
                continue

            scores = []
            for article in news:
                text  = (article.get("headline", "") + " " + article.get("summary", "")).lower()
                words = set(text.split())
                pos   = len(words & _POSITIVE_WORDS)
                neg   = len(words & _NEGATIVE_WORDS)
                total = pos + neg
                scores.append((pos - neg) / total if total > 0 else 0.0)

            avg_score = sum(scores) / len(scores) if scores else 0.0
            results[symbol] = {
                "news_sentiment":      round(avg_score, 3),
                "news_headline_count": len(news),
            }
        except Exception:
            results[symbol] = {"news_sentiment": 0.0, "news_headline_count": 0}

    return results


# ── 3. Sentiment-Trading-Alpha ─────────────────────────────────────────────────

def _fetch_sentiment_signal() -> Optional[dict]:
    """
    Call POST /api/v1/analyze with only SPY and QQQ.

    STA's symbol validator only accepts its registered symbols.
    DEFAULT_TRACKED_SYMBOLS = ["USO", "IBIT", "QQQ", "SPY"] — anything
    else returns a 400 Bad Request. We use SPY+QQQ for macro direction.

    The portfolio-level signal (LONG/SHORT/HOLD) is then applied to all
    our symbols as a market-wide sentiment modifier.

    Timeout: 600s — handles slow first-run analysis on llama/qwen models.
    SPY+QQQ are pre-cached in STA so subsequent calls are fast (~10-20s).
    """
    if not SENTIMENT_BASE:
        return None

    headers = {"Content-Type": "application/json"}
    if SENTIMENT_TOKEN:
        headers["X-Admin-Token"] = SENTIMENT_TOKEN

    payload = {
        "symbols":          STA_SYMBOLS,   # ["SPY", "QQQ"] — always valid
        "max_posts":        20,
        "include_backtest": False,
        "lookback_days":    14,
    }

    try:
        resp = requests.post(
            SENTIMENT_ANALYZE_URL,
            json=payload,
            headers=headers,
            timeout=600,
        )

        if resp.status_code == 404:
            print(
                f"[premarket] Sentiment-Trading-Alpha 404 at {SENTIMENT_ANALYZE_URL}\n"
                f"           Verify backend is running: python run.py (in STA directory)"
            )
            return None

        if resp.status_code in (401, 403):
            print(
                f"[premarket] Sentiment-Trading-Alpha auth error ({resp.status_code}) — "
                f"check SENTIMENT_ADMIN_TOKEN in .env matches ADMIN_API_TOKEN in start_bot.bat"
            )
            return None

        if resp.status_code == 400:
            print(
                f"[premarket] Sentiment-Trading-Alpha 400 Bad Request — "
                f"payload: {payload}\n"
                f"           Response: {resp.text[:200]}"
            )
            return None

        resp.raise_for_status()
        return resp.json()

    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.Timeout:
        print("[premarket] Sentiment-Trading-Alpha timed out after 600s — skipping")
        return None
    except Exception as e:
        print(f"[premarket] Sentiment-Trading-Alpha API error: {e}")
        return None


def get_sentiment_signal(all_symbols: list[str], force_refresh: bool = False) -> dict:
    """
    Get macro market sentiment from STA (SPY+QQQ analysis).

    Returns a dict keyed by ALL symbols (not just SPY/QQQ), each with
    the same portfolio-level signal. This is correct — STA's signal
    represents overall market direction, which applies to all positions.

    sentiment_signal:      "LONG" | "SHORT" | "HOLD"
    sentiment_confidence:  0.0–1.0
    sentiment_conviction:  "HIGH" | "MEDIUM" | "LOW"
    sentiment_directional: -1.0 to +1.0 (from SPY sentiment score)
    """
    global _sentiment_cache, _sentiment_cache_time

    with _sentiment_lock:
        if (
            not force_refresh
            and _sentiment_cache
            and _sentiment_cache_time is not None
            and (datetime.now(timezone.utc) - _sentiment_cache_time).total_seconds()
                < SENTIMENT_MAX_AGE_MINUTES * 60
        ):
            return _sentiment_cache

        print(f"[premarket] Fetching Sentiment-Trading-Alpha (SPY+QQQ macro signal)...")
        raw = _fetch_sentiment_signal()

        if raw is None:
            print("[premarket] Sentiment-Trading-Alpha: unavailable — continuing without it")
            return {}

        trading_signal   = raw.get("trading_signal") or {}
        sentiment_scores = raw.get("sentiment_scores") or {}

        # Portfolio-level signal
        signal_type = str(trading_signal.get("signal_type", "HOLD") or "HOLD").upper()
        confidence  = float(trading_signal.get("confidence_score", 0.0) or 0.0)
        conviction  = str(trading_signal.get("conviction_level", "LOW") or "LOW").upper()

        # Use SPY directional score as the macro sentiment scalar
        spy_entry   = sentiment_scores.get("SPY") or sentiment_scores.get("spy") or {}
        directional = float(spy_entry.get("directional_score", 0.0) or 0.0)

        # Apply the same portfolio signal to every symbol in our watchlist
        # STA tells us the macro direction — we use that for conviction scoring
        results = {}
        for symbol in all_symbols:
            results[symbol.upper()] = {
                "sentiment_signal":      signal_type,
                "sentiment_confidence":  round(confidence, 3),
                "sentiment_conviction":  conviction,
                "sentiment_directional": round(directional, 3),
            }

        _sentiment_cache      = results
        _sentiment_cache_time = datetime.now(timezone.utc)
        print(
            f"[premarket] Sentiment-Trading-Alpha cached | "
            f"macro: {signal_type} ({conviction}) conf={confidence:.2f} "
            f"→ applied to {len(all_symbols)} symbols"
        )
        return results


def trigger_sentiment_async(symbols: list[str], bias: dict = None):
    """
    Fire Sentiment-Trading-Alpha in a background thread at startup.
    Always uses SPY+QQQ only — no symbol validation issues.
    Runs as daemon thread so it never blocks trading.
    """
    def _run():
        try:
            get_sentiment_signal(symbols, force_refresh=True)
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name="sentiment-alpha-bg")
    t.start()
    print(
        "[premarket] Sentiment-Trading-Alpha pipeline started (SPY+QQQ macro signal)\n"
        "           (bot continues if unavailable — result cached when ready)"
    )


# ── 4. Merged enrichment ───────────────────────────────────────────────────────

def enrich_bias(
    bias: dict,
    api_key: str,
    api_secret: str,
    run_sentiment: bool = True,
) -> dict:
    """
    Enriches daily_bias in-place with gap data, news sentiment,
    and (optionally) STA macro sentiment signal.
    """
    symbols = list(bias.keys())
    if not symbols:
        return bias

    print(f"[premarket] Running gap analysis for {len(symbols)} symbols...")
    gaps     = get_premarket_gaps(symbols, api_key, api_secret)
    gap_up   = [s for s, g in gaps.items() if g.get("gap_signal") == "GAP_UP"]
    gap_down = [s for s, g in gaps.items() if g.get("gap_signal") == "GAP_DOWN"]
    print(
        f"[premarket] Gaps: {len(gap_up)} up, {len(gap_down)} down, "
        f"{len(symbols)-len(gap_up)-len(gap_down)} flat"
    )

    print("[premarket] Fetching Alpaca news sentiment...")
    news          = get_alpaca_news_sentiment(symbols, api_key, api_secret)
    positive_news = [s for s, n in news.items() if n.get("news_sentiment", 0) > 0.2]
    negative_news = [s for s, n in news.items() if n.get("news_sentiment", 0) < -0.2]
    print(f"[premarket] News: {len(positive_news)} positive, {len(negative_news)} negative")

    sentiment = {}
    if run_sentiment:
        try:
            sentiment = get_sentiment_signal(symbols)
        except Exception:
            sentiment = {}
        if sentiment:
            # All symbols get the same macro signal — just report it once
            sample = next(iter(sentiment.values()), {})
            print(
                f"[premarket] Sentiment: macro {sample.get('sentiment_signal','?')} "
                f"({sample.get('sentiment_conviction','?')}) "
                f"conf={sample.get('sentiment_confidence',0):.2f} "
                f"→ applied to all {len(symbols)} symbols"
            )
        else:
            print("[premarket] Sentiment-Trading-Alpha: unavailable — continuing without it")

    for symbol in symbols:
        entry = bias.get(symbol, {})
        if gaps.get(symbol):
            entry.update(gaps[symbol])
        if news.get(symbol):
            entry.update(news[symbol])
        if sentiment.get(symbol.upper()):
            entry.update(sentiment[symbol.upper()])
        bias[symbol] = entry

    return bias


# ── Conviction boost helper ────────────────────────────────────────────────────

def premarket_conviction_boost(bias_entry: dict) -> float:
    """
    Calculate a conviction boost (0–25 points) from pre-market signals.
    Called in _process_symbol() conviction scoring.
    """
    boost = 0.0

    gap_pct = abs(bias_entry.get("gap_pct", 0.0))
    if gap_pct >= 2.0:
        boost += 10.0
    elif gap_pct >= 1.0:
        boost += 6.0
    elif gap_pct >= 0.5:
        boost += 3.0

    news_score = bias_entry.get("news_sentiment", 0.0)
    if abs(news_score) >= 0.5:
        boost += 8.0
    elif abs(news_score) >= 0.2:
        boost += 4.0

    sentiment_conf = bias_entry.get("sentiment_confidence", 0.0)
    conviction     = bias_entry.get("sentiment_conviction", "LOW")
    if conviction == "HIGH" and sentiment_conf >= 0.7:
        boost += 7.0
    elif conviction == "MEDIUM" and sentiment_conf >= 0.5:
        boost += 4.0

    return round(min(boost, 25.0), 2)