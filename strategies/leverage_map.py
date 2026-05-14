"""
leverage_map.py — Central registry of leveraged ETF pairs for each signal symbol.

Rules applied:
- Use highest available leverage (3x preferred over 2x)
- Only include pairs with sufficient liquidity (>500K avg daily volume)
- Symbols with NO liquid leveraged ETF trade the underlying directly
- Single-stock leveraged ETFs (NVDL, TSMU etc) are 2x — still better than 1x

Structure:
    LEVERAGE_MAP[signal_symbol] = {
        "bull": "BULL_TICKER",   # buy on BUY/STRONG_BUY signal
        "bear": "BEAR_TICKER",   # buy on SELL/STRONG_SELL signal
        "leverage": 2 or 3,      # leverage multiple of the pair
        "note": "description"
    }

If a symbol is not in the map, the system trades the underlying directly (1x).
"""

LEVERAGE_MAP = {

    # ── Broad Market ──────────────────────────────────────────────────────
    "SPY": {
        "bull": "SPXL",   # Direxion Daily S&P 500 Bull 3x
        "bear": "SPXS",   # Direxion Daily S&P 500 Bear 3x
        "leverage": 3,
        "note": "S&P 500 3x"
    },
    "QQQ": {
        "bull": "TQQQ",   # ProShares UltraPro QQQ 3x
        "bear": "SQQQ",   # ProShares UltraPro Short QQQ 3x
        "leverage": 3,
        "note": "Nasdaq-100 3x"
    },
    "QQQM": {
        "bull": "TQQQ",   # QQQM tracks same index as QQQ — use TQQQ/SQQQ
        "bear": "SQQQ",
        "leverage": 3,
        "note": "Nasdaq-100 3x (QQQM = QQQ same index)"
    },
    "SPMO": {
        "bull": "SPXL",   # No direct momentum 3x — use S&P 3x as proxy
        "bear": "SPXS",
        "leverage": 3,
        "note": "Momentum factor — S&P 3x proxy"
    },

    # ── Semiconductors ────────────────────────────────────────────────────
    "SMH": {
        "bull": "SOXL",   # Direxion Daily Semiconductors Bull 3x
        "bear": "SOXS",   # Direxion Daily Semiconductors Bear 3x
        "leverage": 3,
        "note": "Semiconductor 3x"
    },
    "DRAM": {
        "bull": "SOXL",   # No DRAM-specific leveraged ETF — SMH proxy
        "bear": "SOXS",
        "leverage": 3,
        "note": "Memory/DRAM — semiconductor 3x proxy"
    },
    "NVDA": {
        "bull": "NVDL",   # GraniteShares 2x Long NVDA (higher liquidity than NVDU)
        "bear": "NVDD",   # Direxion Daily NVDA Bear 1x (best available inverse)
        "leverage": 2,
        "note": "NVIDIA single-stock 2x"
    },
    "MU": {
        "bull": "SOXL",   # No MU-specific leveraged ETF — semiconductor proxy
        "bear": "SOXS",
        "leverage": 3,
        "note": "Micron — semiconductor 3x proxy"
    },
    "AMAT": {
        "bull": "SOXL",   # Applied Materials — semiconductor equipment
        "bear": "SOXS",
        "leverage": 3,
        "note": "AMAT — semiconductor 3x proxy"
    },
    "LRCX": {
        "bull": "SOXL",   # Lam Research — semiconductor equipment
        "bear": "SOXS",
        "leverage": 3,
        "note": "LRCX — semiconductor 3x proxy"
    },
    "SNDK": {
        "bull": "SOXL",   # SanDisk/memory — semiconductor proxy
        "bear": "SOXS",
        "leverage": 3,
        "note": "SNDK — semiconductor 3x proxy"
    },
    "TSM": {
        "bull": "TSMU",   # GraniteShares 2x Long TSM
        "bear": "SOXS",   # No direct TSM inverse — semiconductor bear
        "leverage": 2,
        "note": "TSMC 2x long / semiconductor 3x bear"
    },

    # ── Precious Metals ───────────────────────────────────────────────────
    "GLDM": {
        "bull": "UGL",    # ProShares Ultra Gold 2x
        "bear": "GLL",    # ProShares UltraShort Gold -2x
        "leverage": 2,
        "note": "Gold 2x"
    },
    "PSLV": {
        "bull": "AGQ",    # ProShares Ultra Silver 2x
        "bear": "ZSL",    # ProShares UltraShort Silver -2x
        "leverage": 2,
        "note": "Silver 2x"
    },
    "GDXJ": {
        "bull": "JNUG",   # Direxion Daily Junior Gold Miners Bull 2x
        "bear": "JDST",   # Direxion Daily Junior Gold Miners Bear 2x
        "leverage": 2,
        "note": "Junior gold miners 2x"
    },
    "GDMN": {
        "bull": "JNUG",   # Gold miners proxy
        "bear": "JDST",
        "leverage": 2,
        "note": "Gold miners — GDXJ 2x proxy"
    },
    "GDE": {
        "bull": "UGL",    # Gold/equity blend — gold 2x proxy
        "bear": "GLL",
        "leverage": 2,
        "note": "GDE gold-equity blend — gold 2x proxy"
    },
    "ARIS": {
        "bull": "JNUG",   # Silver/gold mining — junior miners proxy
        "bear": "JDST",
        "leverage": 2,
        "note": "Aris Mining — junior miners 2x proxy"
    },
    "AG": {
        "bull": "AGQ",    # First Majestic Silver — silver 2x
        "bear": "ZSL",
        "leverage": 2,
        "note": "First Majestic Silver — silver 2x proxy"
    },
    "PAAS": {
        "bull": "AGQ",    # Pan American Silver — silver 2x
        "bear": "ZSL",
        "leverage": 2,
        "note": "PAAS — silver 2x proxy"
    },
    "SLVP": {
        "bull": "AGQ",    # Silver miners ETF — silver 2x proxy
        "bear": "ZSL",
        "leverage": 2,
        "note": "Silver miners — silver 2x proxy"
    },

    # ── Energy / Commodities ──────────────────────────────────────────────
    "DBC": {
        "bull": "COM",    # Direxion Auspice Broad Commodity ETF (no 3x broad)
        "bear": "DBC",    # No liquid broad commodity inverse — trade underlying
        "leverage": 1,
        "note": "Broad commodities — no quality leveraged pair, trade direct"
    },
    "NANR": {
        "bull": "ERX",    # Direxion Daily Energy Bull 2x (natural resources proxy)
        "bear": "ERY",    # Direxion Daily Energy Bear 2x
        "leverage": 2,
        "note": "Natural resources — energy 2x proxy"
    },
    "REMX": {
        "bull": "REMX",   # No leveraged rare earth ETF — trade underlying
        "bear": "REMX",
        "leverage": 1,
        "note": "Rare earth — no leveraged ETF, trade direct"
    },

    # ── Bitcoin / Crypto ──────────────────────────────────────────────────
    "IBIT": {
        "bull": "BITX",   # Volatility Shares 2x Bitcoin Strategy ETF
        "bear": "BITI",   # ProShares Short Bitcoin ETF (inverse) — fixed from BITU
        "leverage": 2,
        "note": "Bitcoin 2x long / short"
    },

    # ── Financials ────────────────────────────────────────────────────────
    "JPM": {
        "bull": "FAS",    # Direxion Daily Financial Bull 3x
        "bear": "FAZ",    # Direxion Daily Financial Bear 3x
        "leverage": 3,
        "note": "Financials 3x — JPM proxy"
    },

    # ── Tech / AI ─────────────────────────────────────────────────────────
    "PLTR": {
        "bull": "PTIR",   # GraniteShares 2x Long PLTR
        "bear": "SQQQ",   # No direct PLTR inverse — Nasdaq bear proxy
        "leverage": 2,
        "note": "Palantir 2x long / Nasdaq bear"
    },
    "ROBO": {
        "bull": "TQQQ",   # Robotics ETF — no direct leveraged, Nasdaq proxy
        "bear": "SQQQ",
        "leverage": 3,
        "note": "Robotics — Nasdaq 3x proxy"
    },

    # ── Space / Defense ───────────────────────────────────────────────────
    "UFO": {
        "bull": "UFO",    # No leveraged space ETF — trade underlying
        "bear": "UFO",
        "leverage": 1,
        "note": "Space ETF — no leveraged pair, trade direct"
    },
    "RKLB": {
        "bull": "RKLB",   # Rocket Lab — no single-stock leveraged ETF
        "bear": "RKLB",
        "leverage": 1,
        "note": "Rocket Lab — no leveraged ETF, trade direct"
    },

    # ── Uranium ───────────────────────────────────────────────────────────
    "URA": {
        "bull": "URA",    # No leveraged uranium ETF — trade underlying
        "bear": "URA",
        "leverage": 1,
        "note": "Uranium — no leveraged ETF, trade direct"
    },
    "URNM": {
        "bull": "URNM",   # No leveraged uranium miners ETF
        "bear": "URNM",
        "leverage": 1,
        "note": "Uranium miners — no leveraged ETF, trade direct"
    },

    # ── International ─────────────────────────────────────────────────────
    "EWT": {
        "bull": "EWT",    # Taiwan ETF — no leveraged pair
        "bear": "EWT",
        "leverage": 1,
        "note": "Taiwan ETF — no leveraged pair, trade direct"
    },
    "EWJV": {
        "bull": "EWJV",   # Japan Value ETF — no leveraged pair
        "bear": "EWJV",
        "leverage": 1,
        "note": "Japan Value — no leveraged pair, trade direct"
    },

    # ── Alternatives ──────────────────────────────────────────────────────
    "DBMF": {
        "bull": "DBMF",   # Managed futures — no leveraged pair
        "bear": "DBMF",
        "leverage": 1,
        "note": "Managed futures — no leveraged pair, trade direct"
    },
    "GRID": {
        "bull": "GRID",   # Grid infrastructure — no leveraged pair
        "bear": "GRID",
        "leverage": 1,
        "note": "Grid infrastructure — no leveraged pair, trade direct"
    },
    "CEG": {
        "bull": "CEG",    # Constellation Energy — no single-stock leveraged ETF
        "bear": "CEG",
        "leverage": 1,
        "note": "Constellation Energy — no leveraged ETF, trade direct"
    },
}


def get_leveraged_pair(signal_symbol: str) -> dict:
    """
    Return the leveraged ETF pair for a given signal symbol.
    Falls back to trading the underlying directly if no pair exists.
    """
    entry = LEVERAGE_MAP.get(signal_symbol)
    if entry:
        return entry
    # Default: trade underlying directly
    return {
        "bull": signal_symbol,
        "bear": signal_symbol,
        "leverage": 1,
        "note": f"No leveraged pair — trading {signal_symbol} direct"
    }


def is_direct_trade(symbol: str) -> bool:
    """Returns True if bull == bear (no leveraged pair, trade direct)."""
    entry = get_leveraged_pair(symbol)
    return entry["bull"] == entry["bear"]