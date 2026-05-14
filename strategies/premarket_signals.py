"""
premarket_signals.py — Pre-market enrichment layer
────────────────────────────────────────────────────
Three signal sources that run at startup / before_market_opens():

  1. Gap analysis        — pre-market price vs prior close via Alpaca
  2. Alpaca News         — headline sentiment via Alpaca News API (free, same key)
  3. Sentiment-Trading-Alpha — directional score from Sentiment-Trading-Alpha REST API

Each source returns a per-symbol dict that is merged into the daily bias
by enrich_bias() in trend_filtered_orb.py.

Sentiment endpoint configuration
─────────────────────────────────
The endpoint PATH is configurable via .env so you don't have to edit code
when you find the correct route from http://localhost:8000/docs:

  SENTIMENT_API_URL=http://localhost:8000      ← base URL (existing)
  SENTIMENT_ENDPOINT=/analyze                  ← path (new, default /analyze)

If /analyze returns 404, set SENTIMENT_ENDPOINT to the correct path, e.g.:
  SENTIMENT_ENDPOINT=/api/analyze
  SENTIMENT_ENDPOINT=/v1/analyze
  SENTIMENT_ENDPOINT=/signal
"""

from __future__ import annotations

import os
import time
import threading
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

ALPACA_BASE      = "https://data.alpaca.markets"
ALPACA_NEWS_BASE = "https://data.alpaca.markets/v1beta1"

SENTIMENT_BASE     = os.getenv("SENTIMENT_API_URL",   "http://localhost:8000")
SENTIMENT_ENDPOINT = os.getenv("SENTIMENT_ENDPOINT",  "/analyze")
SENTIMENT_TOKEN    = os.getenv("SENTIMENT_ADMIN_TOKEN", "")

# How stale Sentiment-Trading-Alpha is allowed to be before re-fetching (minutes)
SENTIMENT_MAX_AGE_MINUTES = 90

_sentiment_cache:      dict                = {}
_sentiment_cache_time: Optional[datetime]  = None
_sentiment_lock = threading.Lock()


# ── 1. Pre-market gap analysis ─────────────────────────────────────────────────

def get_premarket_gaps(symbols: list[str], api_key: str, api_secret: str) -> dict:
    """
    Fetch pre-market price and volume for each symbol via Alpaca's data API.
    Returns per-symbol dict with gap_pct, gap_signal, gap_vol_ratio.
    """
    results = {}
    headers = {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }

    for symbol in symbols:
        try:
            # Prior close
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

            # Pre-market quote
            quote_resp = requests.get(
                f"{ALPACA_BASE}/v2/stocks/{symbol}/quotes/latest",
                headers=headers,
                timeout=10,
            )
            quote = quote_resp.json().get("quote", {})
            pre_price = float(quote.get("ap", 0) or quote.get("bp", 0) or prior_close)

            gap_pct = ((pre_price - prior_close) / prior_close * 100) if prior_close else 0.0

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
                "news_sentiment":     round(avg_score, 3),
                "news_headline_count": len(news),
            }
        except Exception:
            results[symbol] = {"news_sentiment": 0.0, "news_headline_count": 0}

    return results


# ── 3. Sentiment-Trading-Alpha ─────────────────────────────────────────────────

def _fetch_sentiment_signal(symbols: list[str]) -> Optional[dict]:
    """
    Call Sentiment-Trading-Alpha and return the raw JSON response.

    The endpoint path is read from SENTIMENT_ENDPOINT env var (default /analyze).
    If you get a 404, browse to http://localhost:8000/docs to find the correct
    route, then set SENTIMENT_ENDPOINT in your .env file.

    Returns None if the server is unreachable, token is missing, or any error.
    """
    if not SENTIMENT_BASE:
        return None

    url = f"{SENTIMENT_BASE}{SENTIMENT_ENDPOINT}"

    headers = {"Content-Type": "application/json"}
    if SENTIMENT_TOKEN:
        headers["X-Admin-Token"] = SENTIMENT_TOKEN

    payload = {
        "symbols":      [s.upper() for s in symbols],
        "risk_profile": "aggressive",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)

        if resp.status_code == 404:
            print(
                f"[premarket] Sentiment-Trading-Alpha 404 at {url}\n"
                f"           Browse http://localhost:8000/docs to find the correct path,\n"
                f"           then set SENTIMENT_ENDPOINT=/correct/path in your .env"
            )
            return None

        resp.raise_for_status()
        return resp.json()

    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.HTTPError as e:
        print(f"[premarket] Sentiment-Trading-Alpha HTTP error: {e}")
        return None
    except Exception as e:
        print(f"[premarket] Sentiment-Trading-Alpha API error: {e}")
        return None


def get_sentiment_signal(symbols: list[str], force_refresh: bool = False) -> dict:
    """
    Get per-symbol sentiment from Sentiment-Trading-Alpha bot.
    Results are cached for SENTIMENT_MAX_AGE_MINUTES.

    Returns dict keyed by symbol:
      {
        "sentiment_signal":      str,   "LONG" | "SHORT" | "HOLD"
        "sentiment_confidence":  float, 0.0–1.0
        "sentiment_conviction":  str,   "HIGH" | "MEDIUM" | "LOW"
        "sentiment_directional": float, -1.0 to +1.0
      }
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

        print(f"[premarket] Fetching Sentiment-Trading-Alpha for {symbols}...")
        raw = _fetch_sentiment_signal(symbols)

        if raw is None:
            print("[premarket] Sentiment-Trading-Alpha: unavailable — continuing without it")
            return {}

        results        = {}
        trading_signal = raw.get("trading_signal") or {}
        sentiment_scores = raw.get("sentiment_scores") or {}
        signal_type    = trading_signal.get("signal_type", "HOLD").upper()
        confidence     = float(trading_signal.get("confidence_score", 0.0))
        conviction     = trading_signal.get("conviction_level", "LOW").upper()

        recommendations = trading_signal.get("recommendations") or []
        rec_map = {
            str(r.get("underlying_symbol", r.get("symbol", "")) or "").upper(): r
            for r in recommendations
        }

        for symbol in symbols:
            sym_upper = symbol.upper()
            rec        = rec_map.get(sym_upper, {})
            sent       = sentiment_scores.get(sym_upper, {})
            directional = float(sent.get("directional_score", 0.0) if sent else 0.0)

            action = str(rec.get("action", "") or "").upper()
            if action == "BUY":
                sym_signal = "LONG"
            elif action == "SELL":
                sym_signal = "SHORT"
            elif signal_type in ("LONG", "SHORT", "HOLD"):
                sym_signal = signal_type
            else:
                sym_signal = "HOLD"

            results[sym_upper] = {
                "sentiment_signal":      sym_signal,
                "sentiment_confidence":  round(confidence, 3),
                "sentiment_conviction":  conviction,
                "sentiment_directional": round(directional, 3),
            }

        _sentiment_cache      = results
        _sentiment_cache_time = datetime.now(timezone.utc)
        print(
            f"[premarket] Sentiment-Trading-Alpha cached | "
            f"portfolio: {signal_type} ({conviction}) conf={confidence:.2f}"
        )
        return results


def trigger_sentiment_async(symbols: list[str]):
    """
    Fire Sentiment-Trading-Alpha pipeline in a background thread so it
    doesn't block startup. Result will be cached by 9:45 AM opening.
    """
    def _run():
        try:
            get_sentiment_signal(symbols, force_refresh=True)
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name="sentiment-alpha-bg")
    t.start()
    print(
        "[premarket] Sentiment-Trading-Alpha pipeline started in background "
        "(bot continues if unavailable)"
    )


# ── 4. Merged enrichment ───────────────────────────────────────────────────────

def enrich_bias(
    bias: dict,
    api_key: str,
    api_secret: str,
    run_sentiment: bool = True,
) -> dict:
    """
    Main entry point. Enriches a daily_bias dict in-place with pre-market
    gap data, Alpaca news sentiment, and (optionally) Sentiment-Trading-Alpha.
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
            longs  = [s for s, j in sentiment.items() if j.get("sentiment_signal") == "LONG"]
            shorts = [s for s, j in sentiment.items() if j.get("sentiment_signal") == "SHORT"]
            print(
                f"[premarket] Sentiment: {len(longs)} LONG, {len(shorts)} SHORT, "
                f"{len(symbols)-len(longs)-len(shorts)} HOLD"
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