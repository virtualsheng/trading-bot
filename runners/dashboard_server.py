"""
dashboard_server.py — Trading Bot Dashboard v11
─────────────────────────────────────────────────
Single-account dashboard for the QQQ ORB bot.
No swing mode, no dual-account comparison.

Endpoints:
  /api/bias        — current QQQ EOD signal
  /api/positions   — live Alpaca positions (TQQQ / SQQQ)
  /api/trades      — closed trade history
  /api/stats       — performance stats (last N days)
  /api/equity      — cumulative P&L over time
  /api/regime      — latest QQQ regime readings

Run alongside run_live_combined.py:
    python runners/dashboard_server.py
Then open: http://localhost:5001
"""

import json
import os
import sqlite3
import requests
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import uvicorn

# ── Paths ──────────────────────────────────────────────────────────────────────
BIAS_PATH = os.getenv("BIAS_PATH", "cache/daily_bias.json")
DB_PATH   = os.getenv("DB_PATH",   "cache/trade_journal.db")
API_KEY   = os.getenv("ALPACA_API_KEY",    "")
API_SEC   = os.getenv("ALPACA_API_SECRET", "")
IS_PAPER  = os.getenv("ALPACA_IS_PAPER", "true").lower() == "true"

app = FastAPI(title="ORB Trading Bot Dashboard", version="11.0")
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


def _alpaca_base() -> str:
    return "https://paper-api.alpaca.markets" if IS_PAPER else "https://api.alpaca.markets"


def _alpaca_headers() -> dict:
    return {
        "APCA-API-KEY-ID":     API_KEY,
        "APCA-API-SECRET-KEY": API_SEC,
    }


def _get_alpaca_account() -> dict:
    if not API_KEY or not API_SEC:
        return {}
    try:
        resp = requests.get(
            f"{_alpaca_base()}/v2/account",
            headers=_alpaca_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "portfolio_value": float(data.get("portfolio_value", 0)),
            "cash":            float(data.get("cash", 0)),
            "buying_power":    float(data.get("buying_power", 0)),
            "equity":          float(data.get("equity", 0)),
        }
    except Exception:
        return {}


def _get_alpaca_positions() -> list:
    if not API_KEY or not API_SEC:
        return []
    try:
        resp = requests.get(
            f"{_alpaca_base()}/v2/positions",
            headers=_alpaca_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def _compute_stats(rows: list, days: int) -> dict:
    if not rows:
        return {
            "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "avg_pnl": 0.0, "total_pnl": 0.0,
            "avg_r": 0.0, "profit_factor": 0.0, "days": days,
        }
    wins   = [r for r in rows if r.get("win_loss") == "WIN"]
    losses = [r for r in rows if r.get("win_loss") == "LOSS"]
    pnls   = [r["pnl"] for r in rows if r.get("pnl") is not None]
    r_vals = [r["r_multiple"] for r in rows
              if r.get("r_multiple") is not None
              and r["r_multiple"] == r["r_multiple"]]  # NaN guard

    gross_win  = sum(r["pnl"] for r in wins  if r.get("pnl"))
    gross_loss = abs(sum(r["pnl"] for r in losses if r.get("pnl")))

    return {
        "total_trades":  len(rows),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(rows) * 100, 1) if rows else 0,
        "avg_pnl":       round(sum(pnls) / len(pnls), 2) if pnls else 0,
        "total_pnl":     round(sum(pnls), 2) if pnls else 0,
        "avg_r":         round(sum(r_vals) / len(r_vals), 2) if r_vals else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 0,
        "days":          days,
    }


# ── API endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/account")
def get_account():
    """Alpaca account summary: portfolio value, cash, buying power."""
    data = _get_alpaca_account()
    data["mode"]      = "paper" if IS_PAPER else "live"
    data["signal_symbol"] = "QQQ"
    data["bull_etf"]  = "TQQQ"
    data["bear_etf"]  = "SQQQ"
    return data


@app.get("/api/bias")
def get_bias():
    """Current EOD signal bias for QQQ."""
    bias = _load_bias()
    qqq  = bias.get("QQQ", {})
    return {
        "symbol":           "QQQ",
        "action":           qqq.get("action", "HOLD"),
        "bull_score":       qqq.get("bull_score", 0),
        "bear_score":       qqq.get("bear_score", 0),
        "rsi":              round(qqq.get("rsi", 50.0), 1),
        "vol_ratio":        round(qqq.get("vol_ratio", 1.0), 2),
        "date":             qqq.get("date"),
        "gap_pct":          qqq.get("gap_pct"),
        "gap_signal":       qqq.get("gap_signal"),
        "news_sentiment":   qqq.get("news_sentiment"),
        "sentiment_signal": qqq.get("sentiment_signal"),
        "execution":        "TQQQ" if qqq.get("action") in ("BUY","STRONG_BUY") else
                            "SQQQ" if qqq.get("action") in ("SELL","STRONG_SELL") else
                            "none",
    }


@app.get("/api/positions")
def get_positions():
    """Live Alpaca positions enriched with journal context."""
    alpaca_pos = _get_alpaca_positions()
    # Enrich with journal stop/target/AI confidence
    journal = {
        r["exec_ticker"]: r
        for r in _query("""
            SELECT exec_ticker, initial_stop, initial_target,
                   ai_confidence, regime, entry_time, signal_action,
                   or_high, or_low, direction, quantity
            FROM trades
            WHERE exit_time IS NULL
            ORDER BY entry_time DESC
        """)
    }
    result = []
    for pos in alpaca_pos:
        sym  = pos.get("symbol", "")
        jnl  = journal.get(sym, {})
        qty  = float(pos.get("qty", 0))
        price= float(pos.get("current_price", 0))
        cost = float(pos.get("avg_entry_price", 0))
        pnl  = float(pos.get("unrealized_pl", 0))
        result.append({
            "symbol":         sym,
            "qty":            qty,
            "entry_price":    cost,
            "current_price":  price,
            "unrealized_pnl": round(pnl, 2),
            "unrealized_pct": round((price - cost) / cost * 100, 2) if cost else 0,
            "market_value":   round(qty * price, 2),
            "initial_stop":   jnl.get("initial_stop"),
            "initial_target": jnl.get("initial_target"),
            "ai_confidence":  jnl.get("ai_confidence"),
            "regime":         jnl.get("regime"),
            "signal_action":  jnl.get("signal_action"),
            "entry_time":     jnl.get("entry_time"),
        })
    return result


@app.get("/api/trades")
def get_trades(days: int = 30, limit: int = 100):
    """Closed trade history."""
    return _query("""
        SELECT id, trade_date, symbol, exec_ticker, direction,
               signal_action, entry_price, exit_price, quantity,
               pnl, r_multiple, win_loss, exit_reason,
               ai_confidence, ai_size_mult, regime, orb_suitability,
               or_high, or_low, initial_stop, initial_target,
               bull_score, bear_score, signal_rsi, signal_vol_ratio,
               entry_time, exit_time, ai_narrative,
               portfolio_value_entry
        FROM trades
        WHERE trade_date >= date('now', ?)
          AND exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT ?
    """, (f"-{days} days", limit))


@app.get("/api/stats")
def get_stats(days: int = 30):
    """Performance stats for the last N days."""
    rows = _query("""
        SELECT * FROM trades
        WHERE trade_date >= date('now', ?)
          AND exit_time IS NOT NULL
    """, (f"-{days} days",))
    stats = _compute_stats(rows, days)

    # Add exit reason breakdown
    exit_reasons = {}
    for r in rows:
        reason = r.get("exit_reason", "UNKNOWN")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    stats["exit_reasons"] = exit_reasons

    # Add AI tier breakdown
    ai_tiers = {"high": 0, "mid": 0, "low": 0}
    for r in rows:
        c = r.get("ai_confidence") or 0
        if c >= 0.75:   ai_tiers["high"] += 1
        elif c >= 0.55: ai_tiers["mid"]  += 1
        else:           ai_tiers["low"]  += 1
    stats["ai_tiers"] = ai_tiers

    # Add regime breakdown
    regimes = {}
    for r in rows:
        regime = r.get("regime", "unknown")
        regimes[regime] = regimes.get(regime, 0) + 1
    stats["regimes"] = regimes

    return stats


@app.get("/api/equity")
def get_equity():
    """Cumulative P&L over time (daily)."""
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
        result.append({
            "date":           r["trade_date"],
            "daily_pnl":      round(r["daily_pnl"] or 0, 2),
            "cumulative_pnl": round(cumulative, 2),
        })
    return result


@app.get("/api/regime")
def get_regime():
    """Latest QQQ regime readings."""
    return _query("""
        SELECT symbol, regime, confidence, suitability, reasoning, logged_at
        FROM regime_log
        WHERE id IN (SELECT MAX(id) FROM regime_log GROUP BY symbol)
        ORDER BY logged_at DESC
    """)


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

DASHBOARD_PATH = os.path.join(os.getcwd(), "dashboard.html")
if not os.path.exists(DASHBOARD_PATH):
    DASHBOARD_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "dashboard.html",
    )


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    if os.path.exists(DASHBOARD_PATH):
        with open(DASHBOARD_PATH, encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("""
        <h2 style="font-family:sans-serif;padding:40px">
            dashboard.html not found.<br>
            <small style="color:#888">Place dashboard.html in the project root.</small>
        </h2>
    """)


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "5001"))
    print(f"\n{'='*55}")
    print(f"  ORB Trading Bot Dashboard v11")
    print(f"  http://localhost:{port}")
    print(f"  Mode    : {'PAPER' if IS_PAPER else 'LIVE ⚠️'}")
    print(f"  Signal  : QQQ → TQQQ (bull) / SQQQ (bear)")
    print(f"  Journal : {DB_PATH}")
    print(f"  Bias    : {BIAS_PATH}")
    print(f"{'='*55}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")