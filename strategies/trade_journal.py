"""
trade_journal.py — SQLite Trade Journal
────────────────────────────────────────
Persists every trade with full context for later analysis and ML training.

Schema captures:
  - Signal metadata (source, action, scores)
  - Entry/exit details (price, qty, timing)
  - Market context (regime, volatility, sentiment)
  - AI grading (confidence, size multiplier, flags)
  - Outcome (pnl, win/loss, R-multiple)
  - AI narrative (plain English journal entry)

Usage:
    journal = TradeJournal()
    trade_id = journal.open_trade(...)
    journal.close_trade(trade_id, ...)
    journal.export_csv("trades.csv")
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Optional


# Always resolve relative to the project root (parent of strategies/)
# so the DB ends up in trading-bot/cache/ regardless of where the script is launched from.
_STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_STRATEGY_DIR)
DB_PATH = os.path.join(_PROJECT_ROOT, "cache", "trade_journal.db")


class TradeJournal:

    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    -- Identity
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date      TEXT NOT NULL,
                    symbol          TEXT NOT NULL,
                    exec_ticker     TEXT NOT NULL,
                    direction       TEXT NOT NULL,    -- LONG / SHORT

                    -- Signal
                    signal_action   TEXT,             -- BUY / STRONG_BUY / SELL etc
                    signal_source   TEXT,             -- "technical" / "sentiment" / "combined"
                    bull_score      INTEGER,
                    bear_score      INTEGER,
                    signal_rsi      REAL,
                    signal_vol_ratio REAL,

                    -- Entry
                    entry_time      TEXT,
                    entry_price     REAL,
                    quantity        INTEGER,
                    or_high         REAL,
                    or_low          REAL,
                    or_mid          REAL,
                    breakout_pct    REAL,             -- How far beyond OR at entry

                    -- AI Grading (pre-trade)
                    ai_confidence   REAL,
                    ai_size_mult    REAL,
                    ai_flags        TEXT,             -- JSON array
                    ai_vol_quality  TEXT,
                    ai_pa_quality   TEXT,
                    ai_approved     INTEGER,          -- 1/0

                    -- Regime (at entry)
                    regime          TEXT,
                    regime_conf     REAL,
                    orb_suitability TEXT,
                    stop_adjustment REAL,
                    target_adjustment REAL,

                    -- Risk params
                    initial_stop    REAL,
                    initial_target  REAL,
                    risk_pct        REAL,
                    planned_r       REAL,             -- reward:risk planned

                    -- Exit
                    exit_time       TEXT,
                    exit_price      REAL,
                    exit_reason     TEXT,             -- STOP / TARGET / EOD / MANUAL

                    -- Outcome
                    pnl             REAL,
                    pnl_pct         REAL,
                    r_multiple      REAL,             -- Actual R achieved
                    win_loss        TEXT,             -- WIN / LOSS / BREAKEVEN

                    -- Market context
                    vix_level       REAL,
                    spy_trend       TEXT,             -- UP / DOWN / FLAT
                    sentiment_score REAL,             -- From Jeff's bot (-1 to +1)
                    sentiment_signal TEXT,            -- HOLD / BUY / SELL

                    -- Portfolio context
                    portfolio_value_entry REAL,
                    portfolio_value_exit  REAL,
                    open_positions_count  INTEGER,

                    -- AI narrative (post-trade)
                    ai_narrative    TEXT,

                    -- Meta
                    created_at      TEXT DEFAULT (datetime('now')),
                    updated_at      TEXT DEFAULT (datetime('now'))
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS regime_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    logged_at   TEXT NOT NULL,
                    symbol      TEXT NOT NULL,
                    regime      TEXT,
                    confidence  REAL,
                    suitability TEXT,
                    reasoning   TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_date   ON trades(trade_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)
            """)
            conn.commit()

    def open_trade(
        self,
        symbol:        str,
        exec_ticker:   str,
        direction:     str,
        entry_price:   float,
        quantity:      int,
        or_high:       float,
        or_low:        float,
        or_mid:        float,
        initial_stop:  float,
        initial_target: float,
        risk_pct:      float,
        # Signal context
        signal_action:   str   = "UNKNOWN",
        signal_source:   str   = "technical",
        bull_score:      int   = 0,
        bear_score:      int   = 0,
        signal_rsi:      float = 50.0,
        signal_vol_ratio: float = 1.0,
        # AI grading
        ai_confidence:   float = 0.60,
        ai_size_mult:    float = 1.0,
        ai_flags:        list  = None,
        ai_vol_quality:  str   = "unknown",
        ai_pa_quality:   str   = "unknown",
        ai_approved:     bool  = True,
        # Regime
        regime:           str  = "unknown",
        regime_conf:      float = 0.5,
        orb_suitability:  str  = "moderate",
        stop_adjustment:  float = 1.0,
        target_adjustment: float = 1.0,
        # Portfolio
        portfolio_value:  float = 0.0,
        open_positions:   int   = 0,
    ) -> int:
        """Insert an open trade record. Returns the trade ID."""
        now          = datetime.now()
        or_range     = or_high - or_low
        breakout_ext = abs(entry_price - (or_high if direction == "LONG" else or_low))
        breakout_pct = (breakout_ext / or_range * 100) if or_range > 0 else 0
        risk_dist    = abs(entry_price - initial_stop)
        planned_r    = abs(initial_target - entry_price) / risk_dist if risk_dist > 0 else 0

        with self._connect() as conn:
            cursor = conn.execute("""
                INSERT INTO trades (
                    trade_date, symbol, exec_ticker, direction,
                    signal_action, signal_source, bull_score, bear_score,
                    signal_rsi, signal_vol_ratio,
                    entry_time, entry_price, quantity,
                    or_high, or_low, or_mid, breakout_pct,
                    ai_confidence, ai_size_mult, ai_flags,
                    ai_vol_quality, ai_pa_quality, ai_approved,
                    regime, regime_conf, orb_suitability,
                    stop_adjustment, target_adjustment,
                    initial_stop, initial_target, risk_pct, planned_r,
                    portfolio_value_entry, open_positions_count
                ) VALUES (
                    ?,?,?,?, ?,?,?,?,?,?,
                    ?,?,?, ?,?,?,?,
                    ?,?,?, ?,?,?,
                    ?,?,?, ?,?,
                    ?,?,?,?,
                    ?,?
                )
            """, (
                now.strftime("%Y-%m-%d"), symbol, exec_ticker, direction,
                signal_action, signal_source, bull_score, bear_score,
                signal_rsi, signal_vol_ratio,
                now.isoformat(), entry_price, quantity,
                or_high, or_low, or_mid, round(breakout_pct, 2),
                ai_confidence, ai_size_mult,
                json.dumps(ai_flags or []),
                ai_vol_quality, ai_pa_quality, int(ai_approved),
                regime, regime_conf, orb_suitability,
                stop_adjustment, target_adjustment,
                initial_stop, initial_target, risk_pct, round(planned_r, 2),
                portfolio_value, open_positions,
            ))
            conn.commit()
            return cursor.lastrowid

    def close_trade(
        self,
        trade_id:        int,
        exit_price:      float,
        exit_reason:     str,
        portfolio_value: float,
        # Optional enrichment
        sentiment_score:  float = None,
        sentiment_signal: str   = None,
        vix_level:        float = None,
        spy_trend:        str   = None,
        ai_narrative:     str   = None,
    ):
        """Update trade record with exit data and compute outcome metrics."""
        now = datetime.now()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()

            if not row:
                return

            entry_price   = row["entry_price"]
            initial_stop  = row["initial_stop"]
            initial_target = row["initial_target"]
            direction     = row["direction"]
            quantity      = row["quantity"]

            # P&L
            if direction == "LONG":
                pnl = (exit_price - entry_price) * quantity
            else:
                pnl = (entry_price - exit_price) * quantity

            pnl_pct      = (pnl / row["portfolio_value_entry"] * 100
                            if row["portfolio_value_entry"] else 0)
            risk_dist    = abs(entry_price - initial_stop)
            actual_move  = abs(exit_price - entry_price)
            r_multiple   = actual_move / risk_dist if risk_dist > 0 else 0
            if pnl < 0:
                r_multiple = -r_multiple

            win_loss = ("WIN" if pnl > 0
                        else "LOSS" if pnl < 0
                        else "BREAKEVEN")

            conn.execute("""
                UPDATE trades SET
                    exit_time            = ?,
                    exit_price           = ?,
                    exit_reason          = ?,
                    pnl                  = ?,
                    pnl_pct              = ?,
                    r_multiple           = ?,
                    win_loss             = ?,
                    portfolio_value_exit = ?,
                    sentiment_score      = ?,
                    sentiment_signal     = ?,
                    vix_level            = ?,
                    spy_trend            = ?,
                    ai_narrative         = ?,
                    updated_at           = ?
                WHERE id = ?
            """, (
                now.isoformat(), exit_price, exit_reason,
                round(pnl, 2), round(pnl_pct, 4),
                round(r_multiple, 2), win_loss,
                portfolio_value,
                sentiment_score, sentiment_signal,
                vix_level, spy_trend,
                ai_narrative,
                now.isoformat(),
                trade_id,
            ))
            conn.commit()

    def log_regime(self, symbol: str, regime_data: dict):
        """Log a regime detection event."""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO regime_log (logged_at, symbol, regime,
                    confidence, suitability, reasoning)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(), symbol,
                regime_data.get("regime"),
                regime_data.get("confidence"),
                regime_data.get("orb_suitability"),
                regime_data.get("reasoning"),
            ))
            conn.commit()

    def get_open_trade(self, exec_ticker: str) -> Optional[dict]:
        """
        Return the most recent unclosed trade record for exec_ticker, or None.
        Used by _sync_positions_from_broker to restore original stop/target
        after a bot restart.
        """
        with self._connect() as conn:
            row = conn.execute("""
                SELECT initial_stop, initial_target, entry_price, quantity,
                       or_high, or_low, or_mid, regime, ai_confidence,
                       signal_action, direction
                FROM trades
                WHERE exec_ticker = ?
                  AND exit_time IS NULL
                ORDER BY id DESC
                LIMIT 1
            """, (exec_ticker,)).fetchone()
        return dict(row) if row else None

    def update_entry_price(self, trade_id: int, fill_price: float):
        """Update entry price with actual broker fill price."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE trades SET entry_price = ? WHERE id = ?",
                (round(fill_price, 4), trade_id)
            )
            conn.commit()

    def update_exit_price(self, trade_id: int, fill_price: float):
        """Update exit price and recalculate PnL with actual broker fill price."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT entry_price, quantity FROM trades WHERE id = ?",
                (trade_id,)
            ).fetchone()
            if row:
                entry_price = row[0]
                quantity    = row[1]
                actual_pnl  = round((fill_price - entry_price) * quantity, 2)
                conn.execute(
                    "UPDATE trades SET exit_price = ?, pnl = ? WHERE id = ?",
                    (round(fill_price, 4), actual_pnl, trade_id)
                )
                conn.commit()

    def get_stats(self, days: int = 30) -> dict:
        """Return performance stats for the last N days."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM trades
                WHERE trade_date >= date('now', ?)
                  AND exit_time IS NOT NULL
            """, (f"-{days} days",)).fetchall()

        if not rows:
            return {"total_trades": 0}

        trades  = [dict(r) for r in rows]
        wins    = [t for t in trades if t["win_loss"] == "WIN"]
        losses  = [t for t in trades if t["win_loss"] == "LOSS"]
        pnls    = [t["pnl"] for t in trades if t["pnl"] is not None]

        import numpy as np
        arr        = np.array(pnls) if pnls else np.array([0])
        gross_win  = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))

        # ── Guard against empty or all-null r_multiple list ───────────────
        r_vals = [t["r_multiple"] for t in trades
                  if t.get("r_multiple") is not None
                  and not (isinstance(t["r_multiple"], float) and
                           t["r_multiple"] != t["r_multiple"])]  # NaN check
        avg_r = round(float(np.mean(r_vals)), 2) if r_vals else 0.0

        return {
            "total_trades":   len(trades),
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate":       round(len(wins) / len(trades) * 100, 1),
            "total_pnl":      round(sum(pnls), 2),
            "avg_win":        round(gross_win / len(wins), 2)    if wins   else 0,
            "avg_loss":       round(gross_loss / len(losses), 2) if losses else 0,
            "profit_factor":  round(gross_win / gross_loss, 2)   if gross_loss > 0 else None,
            "avg_r_multiple": avg_r,
            "by_regime":      self._group_by(trades, "regime"),
            "by_symbol":      self._group_by(trades, "symbol"),
            "by_ai_tier":     self._group_by_ai_tier(trades),
        }

    def _group_by(self, trades: list, field: str) -> dict:
        groups = {}
        for t in trades:
            key = t.get(field) or "unknown"
            groups.setdefault(key, {"trades": 0, "pnl": 0, "wins": 0})
            groups[key]["trades"] += 1
            groups[key]["pnl"]    += t.get("pnl") or 0
            if t.get("win_loss") == "WIN":
                groups[key]["wins"] += 1
        return groups

    def _group_by_ai_tier(self, trades: list) -> dict:
        tiers = {"elite(0.9+)": [], "strong(0.75+)": [],
                 "normal(0.65+)": [], "weak(0.55+)": []}
        for t in trades:
            c = t.get("ai_confidence") or 0.6
            if c >= 0.90:
                tiers["elite(0.9+)"].append(t)
            elif c >= 0.75:
                tiers["strong(0.75+)"].append(t)
            elif c >= 0.65:
                tiers["normal(0.65+)"].append(t)
            else:
                tiers["weak(0.55+)"].append(t)
        return {
            k: {
                "count": len(v),
                "win_rate": round(
                    sum(1 for t in v if t["win_loss"] == "WIN") / len(v) * 100, 1
                ) if v else 0,
                "avg_pnl": round(
                    sum(t["pnl"] or 0 for t in v) / len(v), 2
                ) if v else 0,
            }
            for k, v in tiers.items()
        }

    def export_csv(self, path: str = "cache/trades_export.csv"):
        """Export all closed trades to CSV for external analysis."""
        import csv
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_time IS NOT NULL ORDER BY trade_date"
            ).fetchall()
        if not rows:
            print("No closed trades to export.")
            return
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows([dict(r) for r in rows])
        print(f"Exported {len(rows)} trades to {path}")