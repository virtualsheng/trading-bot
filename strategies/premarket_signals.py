"""
premarket_signals.py — Pre-market enrichment layer
────────────────────────────────────────────────────
Three signal sources that run at startup / before_market_opens():

  1. Gap analysis        — pre-market price vs prior close via Alpaca
  2. Alpaca News         — headline sentiment via Alpaca News API (free, same key)
  3. Sentiment-Trading-Alpha — directional score from Sentiment-Trading-Alpha REST API

Sentiment-Trading-Alpha API (from source code analysis of techjeffe/Sentiment-Trading-Alpha):
─────────────────────────────────────────────────────────────────────────────────────────────
  Endpoint:  POST /api/v1/analyze
  Base URL:  SENTIMENT_API_URL env var (default: http://localhost:8000)
  Auth:      X-Admin-Token header (SENTIMENT_ADMIN_TOKEN env var, optional)

  Request body (AnalysisRequest schema):
    {
      "symbols":          ["SPY", "QQQ", ...],   # required, list of tickers
      "max_posts":        50,                     # optional, 1–200, default 50
      "include_backtest": false,                  # optional, skip backtest for speed
      "lookback_days":    14                      # optional, 7–30, default 14
    }

  Response body (AnalysisResponse schema) — key fields we use:
    trading_signal:
      signal_type:       "LONG" | "SHORT" | "HOLD"
      confidence_score:  0.0–1.0
      conviction_level:  "HIGH" | "MEDIUM" | "LOW"
      recommendations:   [ { underlying_symbol, symbol, action, thesis, ... }, ... ]
    sentiment_scores:
      { "<SYMBOL>": { directional_score: -1.0–1.0, confidence: 0.0–1.0, ... }, ... }

  The `recommendations` list contains per-symbol entries where:
    underlying_symbol = signal symbol (e.g. "QQQ")
    symbol            = execution ticker (e.g. "TQQQ")
    action            = "BUY" | "SELL"
    thesis            = "LONG" | "SHORT"

  Note: include_backtest=false is important — backtesting adds 30–60s to the
  response time and we don't need it here. We just need the trading signal.
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

SENTIMENT_BASE  = os.getenv("SENTIMENT_API_URL",   "http://localhost:8000")
SENTIMENT_TOKEN = os.getenv("SENTIMENT_ADMIN_TOKEN", "")

# Full endpoint path confirmed from Sentiment-Trading-Alpha source:
# main.py: app.include_router(analysis_router, prefix="/api/v1")
# routers/analysis.py: router.post("/analyze") → full path = /api/v1/analyze
SENTIMENT_ANALYZE_URL = f"{SENTIMENT_BASE}/api/v1/analyze"

# How stale the cache is allowed to be before re-fetching (minutes)
SENTIMENT_MAX_AGE_MINUTES = 90

_sentiment_cache:      dict                = {}
_sentiment_cache_time: Optional[datetime]  = None
_sentiment_lock = threading.Lock()


# ── 1. Pre-market gap analysis ─────────────────────────────────────────────────

def get_premarket_gaps(symbols: list[str], api_key: str, api_secret: str) -> dict:
    """
    Fetch pre-market price vs prior close for each symbol via Alpaca Data API.
    Returns per-symbol dict with gap_pct, gap_signal, gap_vol_ratio.
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
                "news_sentiment":      round(avg_score, 3),
                "news_headline_count": len(news),
            }
        except Exception:
            results[symbol] = {"news_sentiment": 0.0, "news_headline_count": 0}

    return results


# ── 3. Sentiment-Trading-Alpha ─────────────────────────────────────────────────

def _fetch_sentiment_signal(symbols: list[str]) -> Optional[dict]:
    """
    Call POST /api/v1/analyze on the Sentiment-Trading-Alpha backend.

    Request schema (AnalysisRequest):
      symbols:          list of ticker strings (required)
      max_posts:        50 (enough for signal quality, fast enough for pre-market)
      include_backtest: false (skip — adds 30–60s we don't need)
      lookback_days:    14 (default rolling window)

    Auth: X-Admin-Token header if SENTIMENT_ADMIN_TOKEN is set.

    Returns the raw AnalysisResponse JSON dict, or None on any failure.
    """
    if not SENTIMENT_BASE:
        return None

    headers = {"Content-Type": "application/json"}
    if SENTIMENT_TOKEN:
        headers["X-Admin-Token"] = SENTIMENT_TOKEN

    payload = {
        "symbols":          [s.upper() for s in symbols],
        "max_posts":        50,
        "include_backtest": False,   # skip backtest — we only need the signal
        "lookback_days":    14,
    }

    try:
        resp = requests.post(
            SENTIMENT_ANALYZE_URL,
            json=payload,
            headers=headers,
            timeout=120,   # analysis pipeline can take 60–90s on first run
        )

        if resp.status_code == 404:
            print(
                f"[premarket] Sentiment-Trading-Alpha 404 at {SENTIMENT_ANALYZE_URL}\n"
                f"           Verify the backend is running: python run.py (in the STA directory)\n"
                f"           Expected URL: http://localhost:8000/api/v1/analyze"
            )
            return None

        if resp.status_code == 401 or resp.status_code == 403:
            print(
                f"[premarket] Sentiment-Trading-Alpha auth error ({resp.status_code}) — "
                f"check SENTIMENT_ADMIN_TOKEN in .env"
            )
            return None

        resp.raise_for_status()
        return resp.json()

    except requests.exceptions.ConnectionError:
        # Server not running — fail silently, bot continues without it
        return None
    except requests.exceptions.Timeout:
        print("[premarket] Sentiment-Trading-Alpha timed out after 120s — skipping")
        return None
    except Exception as e:
        print(f"[premarket] Sentiment-Trading-Alpha API error: {e}")
        return None


def get_sentiment_signal(symbols: list[str], force_refresh: bool = False) -> dict:
    """
    Get per-symbol sentiment from Sentiment-Trading-Alpha.
    Results are cached for SENTIMENT_MAX_AGE_MINUTES.

    Maps AnalysisResponse fields to our internal schema:
      sentiment_signal:      "LONG" | "SHORT" | "HOLD"   (from trading_signal.signal_type or per-symbol recommendation)
      sentiment_confidence:  float 0.0–1.0               (from trading_signal.confidence_score)
      sentiment_conviction:  "HIGH" | "MEDIUM" | "LOW"   (from trading_signal.conviction_level)
      sentiment_directional: float -1.0 to +1.0          (from sentiment_scores[symbol].directional_score)
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

        # ── Parse AnalysisResponse ──────────────────────────────────────────
        trading_signal   = raw.get("trading_signal") or {}
        sentiment_scores = raw.get("sentiment_scores") or {}

        # Portfolio-level signal
        signal_type = str(trading_signal.get("signal_type", "HOLD") or "HOLD").upper()
        confidence  = float(trading_signal.get("confidence_score", 0.0) or 0.0)
        conviction  = str(trading_signal.get("conviction_level", "LOW") or "LOW").upper()

        # Per-symbol recommendations: underlying_symbol → {action, thesis, symbol (exec ticker)}
        recommendations = trading_signal.get("recommendations") or []
        rec_map = {
            str(r.get("underlying_symbol") or r.get("symbol") or "").upper(): r
            for r in recommendations
        }

        results = {}
        for symbol in symbols:
            sym_upper = symbol.upper()
            rec        = rec_map.get(sym_upper, {})
            sent_entry = sentiment_scores.get(sym_upper) or {}

            # directional_score from sentiment_scores (per-symbol, -1 to +1)
            directional = float(sent_entry.get("directional_score", 0.0) or 0.0)

            # Per-symbol signal: prefer the recommendation thesis, fall back to portfolio signal
            thesis = str(rec.get("thesis") or "").upper()
            action = str(rec.get("action") or "").upper()

            if thesis in ("LONG", "SHORT"):
                sym_signal = thesis
            elif action == "BUY":
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
    doesn't block startup. Result cached by 9:45 AM ORB window opening.
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
    Enriches a daily_bias dict in-place with pre-market gap data,
    Alpaca news sentiment, and (optionally) Sentiment-Trading-Alpha signal.
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
    Called in _process_symbol() conviction scoring in trend_filtered_orb.py.
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