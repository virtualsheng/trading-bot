"""
leverage_map.py — Signal symbol to leveraged ETF pair mapping
──────────────────────────────────────────────────────────────
Trading bot signal symbols and their 3× leveraged execution ETFs:

  QQQ  → TQQQ (bull) / SQQQ (bear)   Nasdaq-100 3×
  SMH  → SOXL (bull) / SOXS (bear)   Semiconductor 3×
  USO  → UCO (bull) / SCO (bear)   Oil 2x 

Structure:
    LEVERAGE_MAP[signal_symbol] = {
        "bull":     str,   # ETF to buy on BUY signal
        "bear":     str,   # ETF to buy on SELL signal
        "leverage": int,   # leverage multiple
        "note":     str,
    }

To add a new symbol: add an entry to LEVERAGE_MAP below.
"""

LEVERAGE_MAP = {
    "QQQ": {
        "bull":     "TQQQ",
        "bear":     "SQQQ",
        "leverage": 3,
        "note":     "Nasdaq-100 3× — ProShares UltraPro QQQ / Short QQQ",
    },
    "SMH": {
        "bull":     "SOXL",
        "bear":     "SOXS",
        "leverage": 3,
        "note":     "Semiconductor 3× — Direxion Daily Semiconductor Bull/Bear 3×",
    },
    "USO": {
        "bull":     "UCO",
        "bear":     "SCO",
        "leverage": 2,
        "note":     "Oil 2× — ProShares Ultra Bloomberg Crude Oil Bull/Bear 2×",
    },

}


def get_leveraged_pair(signal_symbol: str) -> dict:
    """
    Return the leveraged ETF pair for a signal symbol.
    Falls back to trading the underlying directly if not in map.
    """
    entry = LEVERAGE_MAP.get(signal_symbol.upper())
    if entry:
        return entry
    return {
        "bull":     signal_symbol,
        "bear":     signal_symbol,
        "leverage": 1,
        "note":     f"No leveraged pair — trading {signal_symbol} direct",
    }


def is_direct_trade(symbol: str) -> bool:
    """Returns True if no leveraged pair exists (bull == bear)."""
    entry = get_leveraged_pair(symbol)
    return entry["bull"] == entry["bear"]


def get_all_signal_symbols() -> list[str]:
    """All signal symbols the bot tracks."""
    return list(LEVERAGE_MAP.keys())


def get_all_exec_tickers() -> set[str]:
    """All execution tickers (TQQQ, SQQQ, SOXL, SOXS, UCO, SCO) — used for EOD forced close."""
    tickers = set()
    for pair in LEVERAGE_MAP.values():
        tickers.add(pair["bull"])
        tickers.add(pair["bear"])
    return tickers