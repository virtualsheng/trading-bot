"""
dashboard_server.py — Trading Bot Dashboard API
─────────────────────────────────────────────────
Lightweight FastAPI server that exposes bot state for the dashboard UI.
Run alongside run_live_combined.py:

    python dashboard_server.py

Then open: http://localhost:5001

Reads from:
  - cache/daily_bias.json      → signal bias per symbol
  - cache/trade_journal.db     → trade history + stats
  - cache/daily_bias_backtest.json (if present)

Install deps (if not already):
    pip install fastapi uvicorn --break-system-packages
"""

import json
import os
import sqlite3
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

# Load .env from project root so Alpaca credentials are available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — credentials must be set in environment

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

# ── Paths ──────────────────────────────────────────────────────────────────────
BIAS_PATH    = os.getenv("BIAS_PATH",    "cache/daily_bias.json")
DB_PATH      = os.getenv("DB_PATH",      "cache/trade_journal.db")
SYMBOLS_FILE = os.getenv("SYMBOLS_FILE", "symbols.txt")

app = FastAPI(title="Trading Bot Dashboard", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_bias() -> dict:
    try:
        if os.path.exists(BIAS_PATH):
            with open(BIAS_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _load_symbols() -> list:
    try:
        with open(SYMBOLS_FILE) as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except Exception:
        return []


def _db_connect():
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _query(sql: str, params=()) -> list:
    conn = _db_connect()
    if not conn:
        return []
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── API routes ─────────────────────────────────────────────────────────────────

@app.get("/api/status")
def get_status():
    bias = _load_bias()
    symbols = _load_symbols()
    bias_date = next(iter(bias.values()), {}).get("date") if bias else None
    today = datetime.now().strftime("%Y-%m-%d")

    buys  = [s for s, v in bias.items() if v.get("action") in ("BUY", "STRONG_BUY")]
    sells = [s for s, v in bias.items() if v.get("action") in ("SELL", "STRONG_SELL")]
    holds = [s for s, v in bias.items() if v.get("action") == "HOLD"]

    return {
        "bias_date":       bias_date,
        "bias_current":    bias_date == today,
        "symbols_watched": len(symbols),
        "symbols_loaded":  len(bias),
        "buys":            len(buys),
        "sells":           len(sells),
        "holds":           len(holds),
        "server_time":     datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/bias")
def get_bias():
    bias = _load_bias()
    result = []
    for symbol, data in bias.items():
        result.append({
            "symbol":      symbol,
            "action":      data.get("action", "HOLD"),
            "bull_score":  data.get("bull_score", 0),
            "bear_score":  data.get("bear_score", 0),
            "rsi":         round(data.get("rsi", 50.0), 1),
            "vol_ratio":   round(data.get("vol_ratio", 1.0), 2),
            "date":        data.get("date"),
            "source":      data.get("source", "—"),
            "gap_pct":          data.get("gap_pct"),
            "gap_signal":       data.get("gap_signal"),
            "news_sentiment":   data.get("news_sentiment"),
            "news_count":       data.get("news_headline_count"),
            "sentiment_signal": data.get("sentiment_signal"),
            "sentiment_conf":   data.get("sentiment_confidence"),
        })
    result.sort(key=lambda x: (
        0 if x["action"] in ("STRONG_BUY","BUY") else
        1 if x["action"] == "HOLD" else 2
    ))
    return result


@app.get("/api/positions")
def get_positions():
    """
    Read open positions from Alpaca (live source of truth).
    Falls back to trade_journal.db unclosed trades only if Alpaca
    credentials are not configured.
    """
    api_key    = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_API_SECRET", "")
    is_paper   = os.getenv("ALPACA_IS_PAPER", "true").lower() == "true"

    if not api_key or not api_secret:
        print("[dashboard] WARNING: ALPACA_API_KEY or ALPACA_API_SECRET not set — check .env")
    else:
        print(f"[dashboard] Fetching positions from Alpaca ({'paper' if is_paper else 'LIVE'})...")

    if api_key and api_secret:
        base = "https://paper-api.alpaca.markets" if is_paper else "https://api.alpaca.markets"
        try:
            resp = requests.get(
                f"{base}/v2/positions",
                headers={
                    "APCA-API-KEY-ID":     api_key,
                    "APCA-API-SECRET-KEY": api_secret,
                },
                timeout=10,
            )
            resp.raise_for_status()
            positions = resp.json()
            print(f"[dashboard] Alpaca returned {len(positions)} position(s)")

            # Enrich with journal context (stop, target, AI confidence, regime)
            journal_rows = {
                r["exec_ticker"]: r
                for r in _query("""
                    SELECT exec_ticker, initial_stop, initial_target,
                           ai_confidence, regime, entry_time, signal_action
                    FROM trades
                    WHERE exit_time IS NULL
                    ORDER BY entry_time DESC
                """)
            }

            result = []
            for p in positions:
                sym    = p.get("symbol", "")
                qty    = float(p.get("qty", 0))
                side   = p.get("side", "long").upper()
                cost   = float(p.get("avg_entry_price", 0))
                mkt    = float(p.get("current_price", 0))
                pnl    = float(p.get("unrealized_pl", 0))
                pnl_pct = float(p.get("unrealized_plpc", 0)) * 100
                jrnl   = journal_rows.get(sym, {})

                result.append({
                    "symbol":         sym,
                    "exec_ticker":    sym,
                    "direction":      "LONG" if side == "LONG" else "SHORT",
                    "entry_price":    cost,
                    "current_price":  mkt,
                    "quantity":       int(abs(qty)),
                    "market_value":   float(p.get("market_value", 0)),
                    "unrealized_pnl": round(pnl, 2),
                    "unrealized_pct": round(pnl_pct, 2),
                    "initial_stop":   jrnl.get("initial_stop"),
                    "initial_target": jrnl.get("initial_target"),
                    "ai_confidence":  jrnl.get("ai_confidence"),
                    "regime":         jrnl.get("regime"),
                    "entry_time":     jrnl.get("entry_time"),
                    "signal_action":  jrnl.get("signal_action"),
                    "source":         "alpaca",
                })
            return result

        except Exception as e:
            # Fall through to DB fallback
            print(f"[dashboard] Alpaca positions fetch failed: {e} — using DB fallback")

    # ── DB fallback: only show live trades (exclude backtest by capping lookback) ──
    rows = _query("""
        SELECT symbol, exec_ticker, direction, entry_price, quantity,
               entry_time, initial_stop, initial_target, regime,
               ai_confidence, risk_pct, or_high, or_low, signal_action
        FROM trades
        WHERE exit_time IS NULL
          AND trade_date >= date('now', '-7 days')
        ORDER BY entry_time DESC
    """)
    for r in rows:
        r["source"] = "db"
        r["unrealized_pnl"] = None
        r["current_price"]  = None
    return rows


@app.get("/api/trades/recent")
def get_recent_trades(limit: int = 50):
    rows = _query("""
        SELECT id, trade_date, symbol, exec_ticker, direction,
               entry_time, entry_price, exit_time, exit_price, exit_reason,
               pnl, pnl_pct, r_multiple, win_loss,
               ai_confidence, regime, signal_action, quantity
        FROM trades
        WHERE exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT ?
    """, (limit,))
    return rows


@app.get("/api/stats")
def get_stats(days: int = 30):
    rows = _query("""
        SELECT * FROM trades
        WHERE trade_date >= date('now', ?)
          AND exit_time IS NOT NULL
    """, (f"-{days} days",))

    if not rows:
        return {"total_trades": 0, "days": days}

    wins    = [t for t in rows if t.get("win_loss") == "WIN"]
    losses  = [t for t in rows if t.get("win_loss") == "LOSS"]
    pnls    = [t["pnl"] for t in rows if t.get("pnl") is not None]

    gross_win  = sum(t["pnl"] for t in wins if t.get("pnl"))
    gross_loss = abs(sum(t["pnl"] for t in losses if t.get("pnl")))
    r_vals     = [t["r_multiple"] for t in rows if t.get("r_multiple")]

    # By symbol
    sym_map: dict = {}
    for t in rows:
        s = t["symbol"]
        if s not in sym_map:
            sym_map[s] = {"wins": 0, "losses": 0, "pnl": 0.0}
        if t.get("win_loss") == "WIN":
            sym_map[s]["wins"] += 1
        elif t.get("win_loss") == "LOSS":
            sym_map[s]["losses"] += 1
        if t.get("pnl"):
            sym_map[s]["pnl"] += t["pnl"]

    # By regime
    reg_map: dict = {}
    for t in rows:
        r = t.get("regime") or "unknown"
        if r not in reg_map:
            reg_map[r] = {"wins": 0, "losses": 0, "pnl": 0.0}
        if t.get("win_loss") == "WIN":
            reg_map[r]["wins"] += 1
        elif t.get("win_loss") == "LOSS":
            reg_map[r]["losses"] += 1
        if t.get("pnl"):
            reg_map[r]["pnl"] += t["pnl"]

    # Daily PnL for chart
    daily: dict = {}
    for t in rows:
        d = t.get("trade_date", "")[:10]
        daily[d] = round(daily.get(d, 0.0) + (t.get("pnl") or 0.0), 2)

    return {
        "days":           days,
        "total_trades":   len(rows),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       round(len(wins) / len(rows) * 100, 1) if rows else 0,
        "total_pnl":      round(sum(pnls), 2),
        "avg_win":        round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss":       round(gross_loss / len(losses), 2) if losses else 0,
        "profit_factor":  round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_r":          round(sum(r_vals) / len(r_vals), 2) if r_vals else 0,
        "by_symbol":      sym_map,
        "by_regime":      reg_map,
        "daily_pnl":      dict(sorted(daily.items())),
    }


@app.get("/api/regime")
def get_regime():
    rows = _query("""
        SELECT symbol, regime, confidence, suitability, reasoning, logged_at
        FROM regime_log
        WHERE id IN (
            SELECT MAX(id) FROM regime_log GROUP BY symbol
        )
        ORDER BY logged_at DESC
    """)
    return rows


@app.get("/api/equity")
def get_equity():
    """Daily portfolio value from closed trade P&L cumulative sum."""
    rows = _query("""
        SELECT trade_date, SUM(pnl) as daily_pnl
        FROM trades
        WHERE exit_time IS NOT NULL AND pnl IS NOT NULL
        GROUP BY trade_date
        ORDER BY trade_date ASC
    """)
    cumulative = 0.0
    result = []
    for r in rows:
        cumulative += r["daily_pnl"] or 0.0
        result.append({"date": r["trade_date"], "cumulative_pnl": round(cumulative, 2)})
    return result


# ── Serve the dashboard HTML ───────────────────────────────────────────────────

# dashboard.html lives in the project root.
# The launcher (start_bot.bat) always cd's to the project root before
# starting this script, so os.getcwd() reliably points there.
# We also fall back to the parent of this file's directory for safety.
DASHBOARD_PATH = os.path.join(os.getcwd(), "dashboard.html")
if not os.path.exists(DASHBOARD_PATH):
    # Fallback: one level up from runners/
    DASHBOARD_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "dashboard.html"
    )

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    if os.path.exists(DASHBOARD_PATH):
        with open(DASHBOARD_PATH, encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>Dashboard HTML not found. Place dashboard.html in the same directory.</h1>")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "5001"))
    print(f"\n{'='*60}")
    print(f"  Trading Bot Dashboard")
    print(f"  http://localhost:{port}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")