"""
clear_backtest_trades.py — Remove backtest trades from trade_journal.db
───────────────────────────────────────────────────────────────────────
Run this once from the project root to clean out any trades left over
from backtesting runs so the dashboard only shows live trades.

Usage:
    python clear_backtest_trades.py
    python clear_backtest_trades.py --dry-run   # preview only, no changes
"""

import sqlite3
import os
import sys

DB_PATH = "cache/trade_journal.db"
DRY_RUN = "--dry-run" in sys.argv

if not os.path.exists(DB_PATH):
    print(f"Database not found at {DB_PATH}")
    sys.exit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Count what's there
total    = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
open_pos = conn.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NULL").fetchone()[0]
closed   = conn.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL").fetchone()[0]

print(f"\nDatabase: {DB_PATH}")
print(f"  Total trades    : {total}")
print(f"  Open (no exit)  : {open_pos}  ← these are the backtest leftovers")
print(f"  Closed          : {closed}")

if open_pos == 0:
    print("\nNothing to clean up.")
    conn.close()
    sys.exit(0)

# Show a sample
rows = conn.execute("""
    SELECT id, trade_date, symbol, exec_ticker, direction, entry_price, quantity
    FROM trades WHERE exit_time IS NULL
    ORDER BY id DESC LIMIT 20
""").fetchall()

print(f"\nOpen trades to remove (showing up to 20):")
print(f"  {'ID':<6} {'Date':<12} {'Symbol':<8} {'Exec':<8} {'Dir':<6} {'Price':<10} {'Qty'}")
print(f"  {'-'*6} {'-'*12} {'-'*8} {'-'*8} {'-'*6} {'-'*10} {'-'*6}")
for r in rows:
    print(f"  {r['id']:<6} {r['trade_date'] or '—':<12} {r['symbol']:<8} {r['exec_ticker']:<8} {r['direction']:<6} {r['entry_price']:<10.2f} {r['quantity']}")

if DRY_RUN:
    print(f"\n[DRY RUN] Would delete {open_pos} open trades. Run without --dry-run to apply.")
    conn.close()
    sys.exit(0)

confirm = input(f"\nDelete all {open_pos} open (unclosed) trades? [y/N]: ").strip().lower()
if confirm != 'y':
    print("Aborted.")
    conn.close()
    sys.exit(0)

conn.execute("DELETE FROM trades WHERE exit_time IS NULL")
conn.commit()
remaining = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
conn.close()

print(f"\nDone. Removed {open_pos} backtest trades. {remaining} closed trades remain.")
print("Refresh the dashboard — Open Positions should now show 0.")
