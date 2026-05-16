"""
leverage_map.py — Signal symbol to leveraged ETF pair mapping
──────────────────────────────────────────────────────────────
Trading bot trades QQQ only:
  BUY signal  → BUY TQQQ (ProShares UltraPro QQQ 3×)
  SELL signal → BUY SQQQ (ProShares UltraPro Short QQQ 3×)

Structure:
    LEVERAGE_MAP[signal_symbol] = {
        "bull":     str,   # buy on BUY signal
        "bear":     str,   # buy on SELL signal
        "leverage": int,   # leverage multiple
        "note":     str,
    }
"""

LEVERAGE_MAP = {
    "QQQ": {
        "bull":     "TQQQ",
        "bear":     "SQQQ",
        "leverage": 3,
        "note":     "Nasdaq-100 3x — ProShares UltraPro QQQ / Short QQQ",
    },
}


def get_leveraged_pair(signal_symbol: str) -> dict:
    """
    Return the leveraged ETF pair for a signal symbol.
    Always returns QQQ → TQQQ/SQQQ since that is the only traded symbol.
    Falls back to trading the underlying directly if symbol not in map.
    """
    entry = LEVERAGE_MAP.get(signal_symbol.upper())
    if entry:
        return entry
    # Fallback — trade underlying directly (should never fire for QQQ-only bot)
    return {
        "bull":     signal_symbol,
        "bear":     signal_symbol,
        "leverage": 1,
        "note":     f"No leveraged pair — trading {signal_symbol} direct",
    }


def is_direct_trade(symbol: str) -> bool:
    """
    Returns True if bull == bear (no leveraged pair, trade direct).
    For QQQ this is always False (TQQQ ≠ SQQQ).
    """
    entry = get_leveraged_pair(symbol)
    return entry["bull"] == entry["bear"]