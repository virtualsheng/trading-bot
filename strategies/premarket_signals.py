"""
premarket_signals.py — Pre-market enrichment layer
────────────────────────────────────────────────────
Three signal sources that run at startup / before_market_opens():

  1. Gap analysis        — pre-market price vs prior close via Alpaca
  2. Alpaca News         — headline sentiment via Alpaca News API (free, same key)
  3. Sentiment-Trading-Alpha    — directional score from Sentiment-Trading-Alpha REST API

Each source returns a per-symbol dict that is merged into the daily bias
by _enrich_bias_with_premarket() in trend_filtered_orb.py.

Enriched bias keys added per symbol:
  gap_pct            float   % gap from prior close (positive = gap up)
  gap_vol_ratio      float   pre-market vol / 20d avg pre-market vol
  gap_signal         str     "GAP_UP" | "GAP_DOWN" | "FLAT"
  news_sentiment     float   Alpaca news score  -1.0 to +1.0
  news_headline_count int    # headlines in last 24h
  sentiment_signal        str     "LONG" | "SHORT" | "HOLD"
  sentiment_confidence    float   0.0–1.0
  sentiment_conviction    str     "HIGH" | "MEDIUM" | "LOW"
  sentiment_directional   float   raw directional score -1.0 to +1.0

All three sources fail gracefully — if a source is unavailable the keys
are simply absent from the bias dict and downstream code falls back to
the technical-only conviction score.
"""

from __future__ import annotations

import os
import time
import threading
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

ALPACA_BASE      = "https://data.alpaca.markets"
ALPACA_NEWS_BASE = "https://data.alpaca.markets/v1beta1"
SENTIMENT_BASE        = os.getenv("SENTIMENT_API_URL", "http://localhost:8000")
SENTIMENT_TOKEN       = os.getenv("SENTIMENT_ADMIN_TOKEN", "")

# How stale Sentiment-Trading-Alpha is allowed to be before we skip it (minutes)
SENTIMENT_MAX_AGE_MINUTES = 90


# ── 1. Pre-market gap analysis ─────────────────────────────────────────────────

def get_premarket_gaps(symbols: list[str], api_key: str, api_secret: str) -> dict:
    """
    Fetch pre-market price and volume for each symbol via Alpaca's data API.
    Returns a dict keyed by symbol:
      {
        "gap_pct":       float,   # % gap from prior close
        "gap_vol_ratio": float,   # pre-market vol vs 20d avg
        "gap_signal":    str,     # "GAP_UP" | "GAP_DOWN" | "FLAT"
      }
    """
    if not api_key or not api_secret:
        return {}

    headers  = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
    results  = {}
    now_et   = datetime.now(timezone.utc).astimezone()

    for symbol in symbols:
        try:
            end   = now_et.strftime("%Y-%m-%d")
            start = (now_et - timedelta(days=30)).strftime("%Y-%m-%d")

            daily_url = (
                f"{ALPACA_BASE}/v2/stocks/{symbol}/bars"
                f"?timeframe=1Day&start={start}&end={end}"
                f"&feed=iex&limit=30"
            )
            daily_resp = requests.get(daily_url, headers=headers, timeout=10)
            daily_resp.raise_for_status()
            daily_bars = daily_resp.json().get("bars", [])

            if not daily_bars:
                continue

            prior_close = float(daily_bars[-1]["c"])

            today = now_et.strftime("%Y-%m-%dT04:00:00-05:00")
            now_s = now_et.strftime("%Y-%m-%dT%H:%M:%S%z")

            pm_url = (
                f"{ALPACA_BASE}/v2/stocks/{symbol}/bars"
                f"?timeframe=5Min&start={today}&end={now_s}"
                f"&feed=iex&limit=100"
            )
            pm_resp = requests.get(pm_url, headers=headers, timeout=10)
            pm_resp.raise_for_status()
            pm_bars = pm_resp.json().get("bars", [])

            if not pm_bars:
                results[symbol] = {
                    "gap_pct": 0.0, "gap_vol_ratio": 0.0, "gap_signal": "FLAT"
                }
                continue

            pm_price  = float(pm_bars[-1]["c"])
            pm_volume = sum(float(b["v"]) for b in pm_bars)

            daily_avg_vol = (
                sum(float(b["v"]) for b in daily_bars) / len(daily_bars)
                if daily_bars else 1.0
            )
            avg_pm_vol_proxy = daily_avg_vol * 0.30
            vol_ratio = pm_volume / avg_pm_vol_proxy if avg_pm_vol_proxy > 0 else 1.0

            gap_pct = ((pm_price - prior_close) / prior_close) * 100

            if gap_pct >= 0.5:
                signal = "GAP_UP"
            elif gap_pct <= -0.5:
                signal = "GAP_DOWN"
            else:
                signal = "FLAT"

            results[symbol] = {
                "gap_pct":       round(gap_pct, 3),
                "gap_vol_ratio": round(vol_ratio, 2),
                "gap_signal":    signal,
            }

        except Exception:
            pass

    return results


# ── 2. Alpaca News sentiment ───────────────────────────────────────────────────

def get_alpaca_news_sentiment(symbols: list[str], api_key: str, api_secret: str) -> dict:
    """
    Pull recent headlines from Alpaca News API and score sentiment per symbol.
    Returns dict keyed by symbol:
      {
        "news_sentiment":      float,  # -1.0 to +1.0
        "news_headline_count": int,
      }
    """
    if not api_key or not api_secret:
        return {}

    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
    results = {}

    POSITIVE = [
        "beat", "beats", "record", "surge", "rally", "upgrade", "buyout",
        "bullish", "growth", "profit", "earnings beat", "raises guidance",
        "strong", "gains", "outperform", "breakout", "recovery", "positive",
        "higher", "boost", "demand", "expansion", "win",
    ]
    NEGATIVE = [
        "miss", "misses", "falls", "decline", "downgrade", "recession",
        "bearish", "loss", "layoffs", "cuts guidance", "weak", "drop",
        "underperform", "breakdown", "warning", "negative", "lower",
        "tariff", "sanction", "investigation", "fraud", "default",
    ]

    symbols_str = ",".join(symbols[:10])
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    try:
        url = (
            f"{ALPACA_NEWS_BASE}/news"
            f"?symbols={symbols_str}&start={since}&limit=50&sort=desc"
        )
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        articles = resp.json().get("news", [])
    except Exception:
        return {}

    symbol_articles: dict[str, list] = {s: [] for s in symbols}
    for article in articles:
        for sym in article.get("symbols", []):
            if sym in symbol_articles:
                symbol_articles[sym].append(article)

    for symbol, arts in symbol_articles.items():
        if not arts:
            results[symbol] = {"news_sentiment": 0.0, "news_headline_count": 0}
            continue

        scores = []
        for article in arts:
            text = (
                (article.get("headline") or "")
                + " "
                + (article.get("summary") or "")
            ).lower()

            pos_hits = sum(1 for w in POSITIVE if w in text)
            neg_hits = sum(1 for w in NEGATIVE if w in text)

            if pos_hits + neg_hits == 0:
                scores.append(0.0)
            else:
                scores.append((pos_hits - neg_hits) / (pos_hits + neg_hits))

        avg_score = sum(scores) / len(scores) if scores else 0.0
        results[symbol] = {
            "news_sentiment":      round(avg_score, 3),
            "news_headline_count": len(arts),
        }

    return results


# ── 3. Sentiment-Trading-Alpha integration ─────────────────────────────

_sentiment_cache: dict = {}
_sentiment_cache_time: Optional[datetime] = None
_sentiment_lock = threading.Lock()


def _fetch_sentiment_signal(symbols: list[str]) -> Optional[dict]:
    """
    Call Sentiment-Trading-Alpha /analyze endpoint and return the raw JSON response.
    Returns None if the server is unreachable or the token is missing.
    """
    if not SENTIMENT_BASE:
        return None

    headers = {"Content-Type": "application/json"}
    if SENTIMENT_TOKEN:
        headers["X-Admin-Token"] = SENTIMENT_TOKEN

    payload = {
        "symbols": [s.upper() for s in symbols],
        "risk_profile": "aggressive",
    }

    try:
        resp = requests.post(
            f"{SENTIMENT_BASE}/analyze",
            json=payload,
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
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
        # Use cache if fresh enough — fixed: use total_seconds() not .seconds
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
            return {}

        results = {}
        trading_signal = raw.get("trading_signal") or {}
        sentiment_scores = raw.get("sentiment_scores") or {}
        signal_type = trading_signal.get("signal_type", "HOLD").upper()
        confidence  = float(trading_signal.get("confidence_score", 0.0))
        conviction  = trading_signal.get("conviction_level", "LOW").upper()

        recommendations = trading_signal.get("recommendations") or []
        rec_map = {
            str(r.get("underlying_symbol", r.get("symbol", "")) or "").upper(): r
            for r in recommendations
        }

        for symbol in symbols:
            sym_upper = symbol.upper()
            rec       = rec_map.get(sym_upper, {})
            sent      = sentiment_scores.get(sym_upper, {})

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
    Fire Sentiment-Trading-Alpha pipeline in a background thread so it doesn't block startup.
    """
    def _run():
        try:
            get_sentiment_signal(symbols, force_refresh=True)
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name="sentiment-alpha-bg")
    t.start()
    print("[premarket] Sentiment-Trading-Alpha pipeline started in background (bot continues if unavailable)")


# ── 4. Merged enrichment ──────────────────────────────────────────────────────

def enrich_bias(
    bias: dict,
    api_key: str,
    api_secret: str,
    run_sentiment: bool = True,
) -> dict:
    """
    Main entry point. Enriches a daily_bias dict in-place with pre-market
    gap data, Alpaca news sentiment, and (optionally) Sentiment-Trading-Alpha LLM sentiment.
    """
    symbols = list(bias.keys())
    if not symbols:
        return bias

    print(f"[premarket] Running gap analysis for {len(symbols)} symbols...")
    gaps = get_premarket_gaps(symbols, api_key, api_secret)
    gap_up   = [s for s, g in gaps.items() if g.get("gap_signal") == "GAP_UP"]
    gap_down = [s for s, g in gaps.items() if g.get("gap_signal") == "GAP_DOWN"]
    print(
        f"[premarket] Gaps: {len(gap_up)} up, {len(gap_down)} down, "
        f"{len(symbols)-len(gap_up)-len(gap_down)} flat"
    )

    print(f"[premarket] Fetching Alpaca news sentiment...")
    news = get_alpaca_news_sentiment(symbols, api_key, api_secret)
    positive_news = [s for s, n in news.items() if n.get("news_sentiment", 0) > 0.2]
    negative_news = [s for s, n in news.items() if n.get("news_sentiment", 0) < -0.2]
    print(
        f"[premarket] News: {len(positive_news)} positive, "
        f"{len(negative_news)} negative"
    )

    sentiment = {}
    if run_sentiment:
        try:
            sentiment = get_sentiment_signal(symbols)
        except Exception:
            sentiment = {}
        if sentiment:
            sentiment_longs  = [s for s, j in sentiment.items() if j.get("sentiment_signal") == "LONG"]
            sentiment_shorts = [s for s, j in sentiment.items() if j.get("sentiment_signal") == "SHORT"]
            print(
                f"[premarket] Sentiment: {len(sentiment_longs)} LONG, "
                f"{len(sentiment_shorts)} SHORT, "
                f"{len(symbols)-len(sentiment_longs)-len(sentiment_shorts)} HOLD"
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
    """
    boost = 0.0

    gap_pct       = abs(float(bias_entry.get("gap_pct", 0.0) or 0.0))
    gap_vol_ratio = float(bias_entry.get("gap_vol_ratio", 1.0) or 1.0)
    gap_signal    = bias_entry.get("gap_signal", "FLAT")

    if gap_signal != "FLAT":
        gap_score = min(gap_pct / 0.5 * 2, 8.0)
        vol_mult = min(gap_vol_ratio / 2.0, 1.25) if gap_vol_ratio > 1.0 else 0.8
        boost += gap_score * vol_mult

    news_score = float(bias_entry.get("news_sentiment", 0.0) or 0.0)
    news_count = int(bias_entry.get("news_headline_count", 0) or 0)
    if news_count > 0:
        count_weight = min(news_count / 5.0, 1.5)
        boost += min(abs(news_score) / 0.1 * 0.5, 8.0) * count_weight

    sentiment_signal     = bias_entry.get("sentiment_signal", "HOLD")
    sentiment_conf       = float(bias_entry.get("sentiment_confidence", 0.0) or 0.0)
    sentiment_conviction = bias_entry.get("sentiment_conviction", "LOW")

    if sentiment_signal in ("LONG", "SHORT"):
        conviction_mult = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}.get(sentiment_conviction, 0.3)
        boost += sentiment_conf * 7.0 * conviction_mult

    return round(boost, 2)