"""
dashboard_server.py — Trading Bot Dashboard API v10
─────────────────────────────────────────────────────
Serves the dashboard UI and exposes REST endpoints for both accounts.

Dual-account endpoints:
  /api/{account}/bias        — signal bias (account = orb | swing)
  /api/{account}/positions   — live Alpaca positions
  /api/{account}/trades      — closed trade history
  /api/{account}/stats       — performance stats
  /api/{account}/equity      — cumulative P&L over time
  /api/signals               — shared signal bias (same symbols.txt for both)
  /api/regime                — regime log (shared QQQ regime)
  /api/compare               — side-by-side stats for both accounts

Run alongside run_live_combined.py:
    python runners/dashboard_server.py
Then open: http://localhost:5001
"""

import json
import os
import sqlite3
import requests
from datetime import datetime, timezone
from typing import Optional

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
SYMBOLS_FILE = os.getenv("SYMBOLS_FILE", "symbols.txt")

# Per-account paths — match what run_live_combined.py sets in parameters
ACCOUNT_CONFIG = {
    "orb": {
        "bias_path":    os.getenv("BIAS_PATH_ORB",   "cache/daily_bias_orb.json"),
        "db_path":      os.getenv("DB_PATH_ORB",     "cache/trade_journal_orb.db"),
        "api_key":      os.getenv("ALPACA_API_KEY_ORB",    ""),
        "api_secret":   os.getenv("ALPACA_API_SECRET_ORB", ""),
        "is_paper":     os.getenv("ALPACA_IS_PAPER_ORB", "true").lower() == "true",
        "label":        "ORB (Day Trade)",
        "color":        "#3b82f6",   # blue
    },
    "swing": {
        "bias_path":    os.getenv("BIAS_PATH_SWING", "cache/daily_bias_swing.json"),
        "db_path":      os.getenv("DB_PATH_SWING",   "cache/trade_journal_swing.db"),
        "api_key":      os.getenv("ALPACA_API_KEY_SWING",    ""),
        "api_secret":   os.getenv("ALPACA_API_SECRET_SWING", ""),
        "is_paper":     os.getenv("ALPACA_IS_PAPER_SWING", "true").lower() == "true",
        "label":        "SWING (Overnight)",
        "color":        "#10b981",   # green
    },
}

# Shared bias/regime — use ORB as primary since both accounts use same symbols
SHARED_BIAS_PATH = os.getenv("BIAS_PATH_ORB", "cache/daily_bias_orb.json")

app = FastAPI(title="Trading Bot Dashboard", version="10.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_bias(path: str) -> dict:
    try:
        if os.path.exists(path):
            with open(path) as f:
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


def _db_connect(db_path: str):
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _query(db_path: str, sql: str, params=()) -> list:
    conn = _db_connect(db_path)
    if not conn:
        return []
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_alpaca_positions(account: str) -> list:
    cfg        = ACCOUNT_CONFIG[account]
    api_key    = cfg["api_key"]
    api_secret = cfg["api_secret"]
    is_paper   = cfg["is_paper"]

    if not api_key or not api_secret:
        return []

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
        return resp.json()
    except Exception as e:
        print(f"[dashboard] Alpaca {account} positions failed: {e}")
        return []


def _get_alpaca_account(account: str) -> dict:
    cfg        = ACCOUNT_CONFIG[account]
    api_key    = cfg["api_key"]
    api_secret = cfg["api_secret"]
    is_paper   = cfg["is_paper"]

    if not api_key or not api_secret:
        return {}

    base = "https://paper-api.alpaca.markets" if is_paper else "https://api.alpaca.markets"
    try:
        resp = requests.get(
            f"{base}/v2/account",
            headers={
                "APCA-API-KEY-ID":     api_key,
                "APCA-API-SECRET-KEY": api_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def _compute_stats(rows: list, days: int) -> dict:
    if not rows:
        return {"total_trades": 0, "days": days}

    wins   = [t for t in rows if t.get("win_loss") == "WIN"]
    losses = [t for t in rows if t.get("win_loss") == "LOSS"]
    pnls   = [t["pnl"] for t in rows if t.get("pnl") is not None]

    gross_win  = sum(t["pnl"] for t in wins  if t.get("pnl"))
    gross_loss = abs(sum(t["pnl"] for t in losses if t.get("pnl")))
    r_vals     = [t["r_multiple"] for t in rows if t.get("r_multiple")]

    # By symbol
    sym_map: dict = {}
    for t in rows:
        s = t["symbol"]
        if s not in sym_map:
            sym_map[s] = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0}
        sym_map[s]["trades"] += 1
        if t.get("win_loss") == "WIN":   sym_map[s]["wins"] += 1
        if t.get("win_loss") == "LOSS":  sym_map[s]["losses"] += 1
        if t.get("pnl"):                 sym_map[s]["pnl"] += t["pnl"]

    # By regime
    reg_map: dict = {}
    for t in rows:
        r = t.get("regime") or "unknown"
        if r not in reg_map:
            reg_map[r] = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0}
        reg_map[r]["trades"] += 1
        if t.get("win_loss") == "WIN":   reg_map[r]["wins"] += 1
        if t.get("win_loss") == "LOSS":  reg_map[r]["losses"] += 1
        if t.get("pnl"):                 reg_map[r]["pnl"] += t["pnl"]

    # By AI confidence tier
    ai_map: dict = {}
    for t in rows:
        c = t.get("ai_confidence")
        if c is None:
            tier = "unknown"
        elif c >= 0.90: tier = "0.90+"
        elif c >= 0.75: tier = "0.75-0.89"
        elif c >= 0.65: tier = "0.65-0.74"
        else:           tier = "0.55-0.64"
        if tier not in ai_map:
            ai_map[tier] = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0}
        ai_map[tier]["trades"] += 1
        if t.get("win_loss") == "WIN":   ai_map[tier]["wins"] += 1
        if t.get("win_loss") == "LOSS":  ai_map[tier]["losses"] += 1
        if t.get("pnl"):                 ai_map[tier]["pnl"] += t["pnl"]

    # By exit reason
    exit_map: dict = {}
    for t in rows:
        reason = t.get("exit_reason") or "UNKNOWN"
        if reason not in exit_map:
            exit_map[reason] = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0}
        exit_map[reason]["trades"] += 1
        if t.get("win_loss") == "WIN":   exit_map[reason]["wins"] += 1
        if t.get("win_loss") == "LOSS":  exit_map[reason]["losses"] += 1
        if t.get("pnl"):                 exit_map[reason]["pnl"] += t["pnl"]

    # Daily P&L
    daily: dict = {}
    for t in rows:
        d = (t.get("trade_date") or "")[:10]
        daily[d] = round(daily.get(d, 0.0) + (t.get("pnl") or 0.0), 2)

    # Cumulative equity curve
    equity = []
    cumulative = 0.0
    for d in sorted(daily.keys()):
        cumulative = round(cumulative + daily[d], 2)
        equity.append({"date": d, "cumulative_pnl": cumulative})

    return {
        "days":           days,
        "total_trades":   len(rows),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       round(len(wins) / len(rows) * 100, 1) if rows else 0,
        "total_pnl":      round(sum(pnls), 2),
        "avg_win":        round(gross_win  / len(wins),   2) if wins   else 0,
        "avg_loss":       round(gross_loss / len(losses), 2) if losses else 0,
        "profit_factor":  round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_r":          round(sum(r_vals) / len(r_vals), 2) if r_vals else 0,
        "max_win":        round(max((t["pnl"] for t in wins   if t.get("pnl")), default=0), 2),
        "max_loss":       round(min((t["pnl"] for t in losses if t.get("pnl")), default=0), 2),
        "by_symbol":      sym_map,
        "by_regime":      reg_map,
        "by_ai_tier":     ai_map,
        "by_exit_reason": exit_map,
        "daily_pnl":      dict(sorted(daily.items())),
        "equity_curve":   equity,
    }


# ── Shared endpoints ───────────────────────────────────────────────────────────

@app.get("/api/status")
def get_status():
    result = {}
    for acct, cfg in ACCOUNT_CONFIG.items():
        bias      = _load_bias(cfg["bias_path"])
        acct_info = _get_alpaca_account(acct)
        bias_date = next(iter(bias.values()), {}).get("date") if bias else None
        today     = datetime.now().strftime("%Y-%m-%d")
        buys  = [s for s, v in bias.items() if v.get("action") in ("BUY", "STRONG_BUY")]
        sells = [s for s, v in bias.items() if v.get("action") in ("SELL", "STRONG_SELL")]
        result[acct] = {
            "label":           cfg["label"],
            "is_paper":        cfg["is_paper"],
            "bias_date":       bias_date,
            "bias_current":    bias_date == today,
            "buys":            len(buys),
            "sells":           len(sells),
            "portfolio_value": acct_info.get("portfolio_value"),
            "cash":            acct_info.get("cash"),
            "equity":          acct_info.get("equity"),
        }
    result["symbols_watched"] = len(_load_symbols())
    result["server_time"]     = datetime.now(timezone.utc).isoformat()
    return result


@app.get("/api/signals")
def get_signals():
    """Shared signal bias table (same symbols.txt for both accounts)."""
    bias = _load_bias(SHARED_BIAS_PATH)
    result = []
    for symbol, data in bias.items():
        result.append({
            "symbol":           symbol,
            "action":           data.get("action", "HOLD"),
            "bull_score":       data.get("bull_score", 0),
            "bear_score":       data.get("bear_score", 0),
            "rsi":              round(data.get("rsi", 50.0), 1),
            "vol_ratio":        round(data.get("vol_ratio", 1.0), 2),
            "date":             data.get("date"),
            "gap_pct":          data.get("gap_pct"),
            "gap_signal":       data.get("gap_signal"),
            "news_sentiment":   data.get("news_sentiment"),
            "news_count":       data.get("news_headline_count"),
            "sentiment_signal": data.get("sentiment_signal"),
            "sentiment_conf":   data.get("sentiment_confidence"),
        })
    result.sort(key=lambda x: (
        0 if x["action"] in ("STRONG_BUY", "BUY") else
        1 if x["action"] == "HOLD" else 2
    ))
    return result


@app.get("/api/regime")
def get_regime():
    db_path = ACCOUNT_CONFIG["orb"]["db_path"]
    return _query(db_path, """
        SELECT symbol, regime, confidence, suitability, reasoning, logged_at
        FROM regime_log
        WHERE id IN (SELECT MAX(id) FROM regime_log GROUP BY symbol)
        ORDER BY logged_at DESC
    """)


@app.get("/api/compare")
def get_compare(days: int = 30):
    """Side-by-side stats for both accounts."""
    result = {}
    for acct, cfg in ACCOUNT_CONFIG.items():
        rows = _query(cfg["db_path"], """
            SELECT * FROM trades
            WHERE trade_date >= date('now', ?)
              AND exit_time IS NOT NULL
        """, (f"-{days} days",))
        stats = _compute_stats(rows, days)
        acct_info = _get_alpaca_account(acct)
        stats["label"]           = cfg["label"]
        stats["color"]           = cfg["color"]
        stats["portfolio_value"] = acct_info.get("portfolio_value")
        stats["cash"]            = acct_info.get("cash")
        result[acct] = stats
    return result


# ── Per-account endpoints ──────────────────────────────────────────────────────

@app.get("/api/{account}/positions")
def get_positions(account: str):
    if account not in ACCOUNT_CONFIG:
        return []
    cfg = ACCOUNT_CONFIG[account]

    alpaca_positions = _get_alpaca_positions(account)

    # Enrich with journal context
    journal_rows = {
        r["exec_ticker"]: r
        for r in _query(cfg["db_path"], """
            SELECT exec_ticker, initial_stop, initial_target,
                   ai_confidence, regime, entry_time, signal_action,
                   or_high, or_low, direction, quantity
            FROM trades
            WHERE exit_time IS NULL
            ORDER BY entry_time DESC
        """)
    }

    result = []
    for p in alpaca_positions:
        sym     = p.get("symbol", "")
        qty     = float(p.get("qty", 0))
        cost    = float(p.get("avg_entry_price", 0))
        mkt     = float(p.get("current_price", 0))
        pnl     = float(p.get("unrealized_pl", 0))
        pnl_pct = float(p.get("unrealized_plpc", 0)) * 100
        jrnl    = journal_rows.get(sym, {})

        result.append({
            "symbol":         sym,
            "direction":      "LONG",
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
            "or_high":        jrnl.get("or_high"),
            "or_low":         jrnl.get("or_low"),
        })

    return result


@app.get("/api/{account}/trades")
def get_trades(account: str, limit: int = 200, days: int = 90):
    if account not in ACCOUNT_CONFIG:
        return []
    return _query(ACCOUNT_CONFIG[account]["db_path"], """
        SELECT id, trade_date, symbol, exec_ticker, direction,
               entry_time, entry_price, exit_time, exit_price, exit_reason,
               pnl, pnl_pct, r_multiple, win_loss,
               ai_confidence, ai_size_mult, regime, signal_action,
               quantity, initial_stop, initial_target,
               or_high, or_low, bull_score, bear_score, signal_rsi,
               orb_suitability, ai_narrative
        FROM trades
        WHERE exit_time IS NOT NULL
          AND trade_date >= date('now', ?)
        ORDER BY exit_time DESC
        LIMIT ?
    """, (f"-{days} days", limit))


@app.get("/api/{account}/stats")
def get_stats(account: str, days: int = 30):
    if account not in ACCOUNT_CONFIG:
        return {}
    rows = _query(ACCOUNT_CONFIG[account]["db_path"], """
        SELECT * FROM trades
        WHERE trade_date >= date('now', ?)
          AND exit_time IS NOT NULL
    """, (f"-{days} days",))
    return _compute_stats(rows, days)


@app.get("/api/{account}/equity")
def get_equity(account: str):
    if account not in ACCOUNT_CONFIG:
        return []
    rows = _query(ACCOUNT_CONFIG[account]["db_path"], """
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
    return HTMLResponse("<h1>dashboard.html not found in project root.</h1>")


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "5001"))
    print(f"\n{'='*60}")
    print(f"  Trading Bot Dashboard v10 — Dual Account")
    print(f"  http://localhost:{port}")
    print(f"  ORB  journal : {ACCOUNT_CONFIG['orb']['db_path']}")
    print(f"  SWING journal: {ACCOUNT_CONFIG['swing']['db_path']}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")