"""
premarket_signals.py — Pre-market enrichment layer
────────────────────────────────────────────────────
Two signal sources that run at startup / before_market_opens():

  1. Gap analysis   — pre-market price vs prior close via Alpaca Data API
  2. Alpaca News    — headline sentiment via Alpaca News API (free, same key)

Sentiment-Trading-Alpha has been removed. It caused persistent 400/401/timeout
errors and added marginal signal value vs the complexity cost. Gap analysis and
Alpaca News are reliable, free, and add genuine ORB conviction context:

  GAP_UP   + BUY bias  → conviction boost (aligned)
  GAP_DOWN + SELL bias → conviction boost (aligned)
  GAP_DOWN + BUY bias  → conviction penalty (counter-signal)
  Positive news        → conviction boost
  Negative news        → conviction penalty

Both run synchronously in before_market_opens() — they're fast (< 5s total).
"""

from __future__ import annotations

import os
import requests
from datetime import datetime, timedelta, timezone

ALPACA_BASE      = "https://data.alpaca.markets"
ALPACA_NEWS_BASE = "https://data.alpaca.markets/v1beta1"

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


# ── 1. Gap analysis ────────────────────────────────────────────────────────────

def get_premarket_gaps(symbols: list[str], api_key: str, api_secret: str) -> dict:
    """
    Fetch pre-market price vs prior close for each symbol via Alpaca Data API.
    Returns per-symbol dict with gap_pct, gap_signal.
    """
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
                "gap_pct":    round(gap_pct, 3),
                "gap_signal": gap_signal,
            }
        except Exception:
            results[symbol] = {"gap_signal": "FLAT", "gap_pct": 0.0}

    return results


# ── 2. Alpaca News sentiment ───────────────────────────────────────────────────

def get_alpaca_news_sentiment(symbols: list[str], api_key: str, api_secret: str) -> dict:
    """Score last 24h headlines from Alpaca News for each symbol."""
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

            results[symbol] = {
                "news_sentiment":      round(sum(scores) / len(scores), 3),
                "news_headline_count": len(news),
            }
        except Exception:
            results[symbol] = {"news_sentiment": 0.0, "news_headline_count": 0}

    return results


# ── 3. Merged enrichment ───────────────────────────────────────────────────────

def enrich_bias(
    bias: dict,
    api_key: str,
    api_secret: str,
    run_sentiment: bool = True,   # kept for API compatibility, ignored
) -> dict:
    """
    Enriches daily_bias in-place with gap data and Alpaca news sentiment.
    run_sentiment parameter is accepted but ignored (STA removed).
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

    for symbol in symbols:
        entry = bias.get(symbol, {})
        if gaps.get(symbol):
            entry.update(gaps[symbol])
        if news.get(symbol):
            entry.update(news[symbol])
        bias[symbol] = entry

    return bias


def trigger_sentiment_async(symbols: list[str], bias: dict = None):
    """No-op — STA removed. Kept so existing call sites don't break."""
    pass


# ── Conviction boost ───────────────────────────────────────────────────────────

def premarket_conviction_boost(bias_entry: dict) -> float:
    """
    Calculate conviction boost (0–18 points) from gap and news signals.
    STA contribution removed.
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

    return round(min(boost, 18.0), 2)