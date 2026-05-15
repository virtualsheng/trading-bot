# Trading Bot — v0.1.10

An AI-enhanced algorithmic trading system for US equities and leveraged ETFs. Combines momentum-based technical analysis with Opening Range Breakout (ORB) execution, filtered and sized by a local LLM running via Ollama.
---

## Table of Contents

- [Overview](#overview)
- [Trade Model](#trade-model)
- [Dual Account Mode](#dual-account-mode)
- [Architecture](#architecture)
- [Signal Pipeline](#signal-pipeline)
- [AI Layer](#ai-layer)
- [Pre-Market Enrichment](#pre-market-enrichment)
- [Earnings Filter](#earnings-filter)
- [Trade Journal](#trade-journal)
- [Dashboard](#dashboard)
- [Project Structure](#project-structure)
- [Leveraged ETF Map](#leveraged-etf-map)
- [Daily Schedule](#daily-schedule)
- [Setup](#setup)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)
- [Notifications](#notifications)
- [Symbols](#symbols)
- [Risk Management](#risk-management)
- [Known Limitations](#known-limitations)
- [Disclaimer](#disclaimer)

---

## Overview

A fully automated paper/live trading bot built on LumiBot and Alpaca. The system runs a two-stage daily cycle:

**Stage 1 — EOD technical signal (3:50 PM and ~4:05 PM ET)**
Scans all symbols in `symbols.txt` using EMA crossovers (2/3/5), RSI(14), MACD, SMA50/200, and volume ratio to generate a directional bias per symbol: `BUY`, `STRONG_BUY`, `SELL`, `STRONG_SELL`, or `HOLD`. Results are written to `cache/daily_bias.json`. A preliminary pass runs at 3:50 PM while the market is still open, and a final confirmed pass runs via `after_market_closes()` at approximately 4:05 PM using official closing prices.

**Stage 2 — Morning execution (9:45 AM – noon ET, 2-min iterations)**
Watches for price to break above the Opening Range High (bullish setups) or below the Opening Range Low (bearish setups). Only executes breakouts aligned with the prior-day bias, then routes the order to the appropriate leveraged ETF. Before any order is placed, a local LLM grades the setup quality and the current market regime adjusts position sizing and stop/target levels.

---

## Trade Model

**We never short-sell. Every order submitted is a BUY.**

| Signal | Breakout direction | Execution |
|---|---|---|
| BUY / STRONG_BUY | Price > OR High + 0.1% | BUY bull leveraged ETF (e.g. QQQ → TQQQ) |
| SELL / STRONG_SELL | Price < OR Low − 0.1% | BUY inverse ETF (e.g. IBIT → BITI) |
| SELL / STRONG_SELL | No inverse ETF available | Skip — cannot express bearish view |
| HOLD | Price > OR High + 0.1% | BUY bull ETF at 0.5× size |
| HOLD | Inside range or breakdown | Skip |

For inverse ETF positions:
- We BUY the inverse ETF and hold it as a LONG position
- Stop is below entry price, target is above (we want the inverse ETF to go up)
- Position closes at EOD (inverse ETFs are never held overnight)
- Position also closes if the underlying symbol later gets a BUY signal

For bull ETF and direct-trade positions:
- Direct-trade symbols (no leveraged pair) can be held overnight
- Position closes when the underlying symbol gets a SELL signal, or stop/target is hit

---

## Dual Account Mode

Two separate Alpaca accounts run simultaneously, each as its own process.

**LumiBot does not support multiple live strategies in one process.** Each account runs as a completely independent `python run_live_combined.py --account orb/swing` invocation. `start_bot.bat` launches both automatically.

| Account | Flag | Mode | ETFs | Overnight |
|---|---|---|---|---|
| ORB | `--account orb` | `swing_mode=False` | Leveraged + inverse ETFs | Direct-trade longs only |
| SWING | `--account swing` | `swing_mode=True` | **No leveraged or inverse ETFs** | All direct-trade positions |

**Swing mode rules:**
- Only enters on upside breakouts (never bearish)
- Only buys the direct underlying (`get_swing_ticker()` returns the stock, never the leveraged ETF)
- 90-day cooldown between selling the same symbol
- Force-sell override fires when conviction ≥ 85, bear_score ≥ 5, and signal is STRONG_SELL

Each account has its own:
- Alpaca API credentials (`ALPACA_API_KEY_ORB` / `ALPACA_API_KEY_SWING`)
- Bias cache (`cache/daily_bias_orb.json` / `cache/daily_bias_swing.json`)
- Trade journal (`cache/trade_journal_orb.db` / `cache/trade_journal_swing.db`)
- Log file (`logs/bot_orb_*.log` / `logs/bot_swing_*.log`)

---

## Architecture

```
symbols.txt
    │
    ▼
signal_engine.py          ← EMA/RSI/MACD/SMA technical scan (EOD)
    │
    ▼
daily_bias_{orb|swing}.json   ← BUY / SELL / HOLD per symbol (persisted)
    │
    ├── premarket_signals.py  ← Gap analysis + Alpaca News enrichment
    │
    ▼
trend_filtered_orb.py     ← Main strategy (LumiBot) — one instance per account
    │
    ├── ai_engine.py          ← Ollama: setup grader + regime detector
    ├── leverage_map.py       ← Signal symbol → leveraged ETF pair
    ├── earnings_filter.py    ← Skip symbols with earnings within 48h
    └── trade_journal.py      ← SQLite: full trade context + stats
```

---

## Signal Pipeline

### Technical Indicators (signal_engine.py)

Each symbol is scored daily using:

- **EMA 2/3/5 crossovers** — short-term momentum stacking
- **RSI(14)** — momentum extremes
- **MACD histogram** — trend confirmation
- **SMA 50 / SMA 200** — trend context
- **Volume ratio** — above-average volume confirms breakouts

| Condition | Signal |
|---|---|
| bull_score ≥ 5, RSI < 68, vol_ratio > 1.1 | STRONG_BUY |
| bull_score ≥ 4, RSI < 62 | BUY |
| bear_score ≥ 5, RSI > 32, vol_ratio > 1.1 | STRONG_SELL |
| bear_score ≥ 4, RSI > 38 | SELL |
| Otherwise | HOLD |

### ORB Execution (trend_filtered_orb.py)

The Opening Range is established from the first three 5-minute bars (9:30–9:45 AM). Entry fires when:

1. Prior-day bias is BUY/STRONG_BUY and price breaks above OR High × 1.001
2. Prior-day bias is SELL/STRONG_SELL and price breaks below OR Low × 0.999 (and inverse ETF exists)
3. Prior-day bias is HOLD and price breaks above OR High × 1.001 (half size, SWING mode only if unleveraged)

A 0.1% minimum breakout filter (`min_breakout_pct`) prevents entries on noise at the OR boundary.

---

## AI Layer

### Setup Grader (grade_setup)

Before every entry, Ollama (`llama3.2:3b`) grades the breakout setup using the last 25 five-minute candles, OR boundaries, breakout extension, and volume ratio. Returns confidence 0.0–1.0 and a size multiplier:

| Confidence | Size multiplier |
|---|---|
| ≥ 0.90 | 2.0× |
| ≥ 0.75 | 1.5× |
| ≥ 0.65 | 1.0× |
| ≥ 0.55 | 0.5× |
| < 0.55 | Skip |

If Ollama is unavailable, defaults to 0.60 confidence / 0.5× size so trades still execute at reduced risk.

### Regime Detector (detect_regime)

Every 30 minutes, and at market open, the regime is classified using 5 bars each of 5m/15m/1H data plus RSI and ATR. Possible regimes: `trending_up`, `trending_down`, `ranging`, `volatile`, `mean_reversion`, `low_liquidity`.

- **volatile** regime: size reduced to 0.75×
- **low_liquidity** regime (≥ 0.70 confidence): symbol skipped
- **poor ORB suitability** (≥ 0.70 confidence): symbol skipped
- Regime adjusts stop and target distances via `stop_adjustment` and `target_adjustment` multipliers

### Trade Narrator (narrate_trade)

After a position closes, Ollama writes a 2-3 sentence journal entry. Stored in the SQLite trade journal.

---

## Pre-Market Enrichment

`strategies/premarket_signals.py` enriches the bias before the ORB window opens using two fast, reliable sources:

**1. Gap Analysis (Alpaca Data API)**
Compares pre-market quote to prior close. Gaps ≥ 0.5% are tagged `GAP_UP` or `GAP_DOWN`. Aligned gaps boost conviction; misaligned gaps reduce it.

**2. Alpaca News Sentiment**
Scans last 24h of headlines for each symbol. Positive/negative keyword scoring adjusts conviction. Uses the same Alpaca API key — no additional credentials needed.

Both run in `before_market_opens()` and complete in under 5 seconds total.

---

## Earnings Filter

`strategies/earnings_filter.py` fetches the next earnings date from Yahoo Finance and blocks new entries within 48 hours of a scheduled report. Falls open on network failure. ETFs and macro symbols log at DEBUG level — "no earnings found" is expected, not an error.

---

## Trade Journal

Every trade is logged to a per-account SQLite database with:

- Entry/exit price, quantity, P&L, R-multiple
- OR High, OR Low, OR Mid at time of entry
- Signal action, bull/bear score, RSI, volume ratio
- AI confidence, size multiplier, flags, volume quality
- Market regime, ORB suitability, stop/target adjustments
- Ollama-generated narrative (post-trade)

---

## Dashboard

FastAPI server on port 5001 (`runners/dashboard_server.py`). Serves `dashboard.html` with a dual-account view:

- **Dashboard** — portfolio status, equity curves, open positions for both accounts
- **Compare** — head-to-head KPIs, animated bar charts, by-symbol / by-regime / by-AI-tier breakdown tables, overlaid equity curves
- **Positions** — live positions from Alpaca enriched with stop, target, AI confidence, regime
- **Trade Log** — filterable by symbol / result / exit reason, with full trade details
- **Performance** — equity curve, daily P&L bar chart, exit reason breakdown — per account
- **Signals** — shared bias table with RSI, vol ratio, gap %, news sentiment
- **Regime** — latest QQQ regime readings

All tables are sortable by clicking column headers. Auto-refreshes every 2 minutes.

---

## Project Structure

```
trading-bot/
│
├── strategies/
│   ├── ai_engine.py             # Ollama: setup grader, regime detector, narrator
│   ├── earnings_filter.py       # Earnings calendar filter (Yahoo Finance)
│   ├── leverage_map.py          # Symbol → leveraged ETF pair registry
│   ├── premarket_signals.py     # Gap analysis + Alpaca News enrichment
│   ├── signal_engine.py         # EMA/RSI/MACD/SMA technical signal generator
│   ├── trade_journal.py         # SQLite trade journaling and stats
│   └── trend_filtered_orb.py    # ★ Main live strategy (full pipeline)
│
├── runners/
│   ├── dashboard_server.py      # FastAPI dashboard (port 5001)
│   ├── run_live_combined.py     # ★ Main live runner (--account orb|swing)
│   └── run_backtest_combined.py # Backtest runner
│
├── notifications/
│   ├── discord.py               # Discord webhook
│   ├── emailer.py               # SMTP email
│   └── telegram.py              # Telegram bot
│
├── cache/
│   ├── daily_bias_orb.json      # ORB account bias cache
│   ├── daily_bias_swing.json    # SWING account bias cache
│   ├── trade_journal_orb.db     # ORB account trade journal (SQLite)
│   └── trade_journal_swing.db   # SWING account trade journal (SQLite)
│
├── logs/
│   ├── bot_orb_YYYYMMDD.log     # ORB account session log
│   └── bot_swing_YYYYMMDD.log   # SWING account session log
│
├── symbols.txt                  # Master symbol list (shared by both accounts)
├── check_env.py                 # Validates .env keys before launch
├── start_bot.bat                # One-click Windows launcher (3 processes)
├── .env                         # API keys — never commit
└── .env.example                 # Template
```

---

## Leveraged ETF Map

`strategies/leverage_map.py` maps each signal symbol to its highest-available leveraged ETF pair. Bull ETF is bought on BUY signals; bear/inverse ETF is bought on SELL signals. **Swing mode always trades the direct underlying — never leveraged or inverse ETFs.**

| Signal Symbol | Bull ETF | Bear ETF | Leverage | Notes |
|---|---|---|---|---|
| QQQ / QQQM | TQQQ | SQQQ | 3× | Nasdaq-100 |
| SPY / SPMO | SPXL | SPXS | 3× | S&P 500 |
| SMH / DRAM / MU / AMAT / LRCX / SNDK | SOXL | SOXS | 3× | Semiconductors |
| NVDA | NVDL | NVDD | 2× | Single-stock |
| TSM | TSMU | SOXS | 2× | Single-stock |
| PLTR | PTIR | SQQQ | 2× | Single-stock |
| GLDM / GDE | UGL | GLL | 2× | Gold |
| PSLV / AG / PAAS / SLVP | AGQ | ZSL | 2× | Silver |
| GDXJ / GDMN / ARIS | JNUG | JDST | 2× | Junior gold miners |
| IBIT | BITX | BITI | 2× | Bitcoin |
| JPM | FAS | FAZ | 3× | Financials |
| NANR | ERX | ERY | 2× | Energy / natural resources |
| DBC | COM | DBC | 1× | Broad commodities — no quality inverse |
| UFO / RKLB / URA / URNM / EWT / EWJV / EWY / DBMF / GRID / CEG / REMX / GEV | direct | direct | 1× | No leveraged pair |

Symbols with `bull == bear` (direct-trade) cannot express a bearish view. SELL signals on these symbols skip new entries but still close existing long positions.

---

## Daily Schedule

| Time (ET) | Event |
|---|---|
| Script start | Ollama warmup, bias refresh, earnings cache pre-warm |
| ~9:00 AM | `before_market_opens()`: earnings cache cleared, regime pre-warmed, gap + news enrichment |
| 9:30 AM | Position sync from Alpaca broker |
| 9:45 AM | ORB entry window opens — iterations switch from 5-min to **2-min** |
| 9:45 AM – noon | ORB entries evaluated every 2 minutes |
| Noon | ORB window closes — back to 5-min iterations |
| Every 30 min | Regime detection refresh (QQQ) |
| 3:45 PM | Leveraged and inverse ETFs closed (ORB account) |
| 3:50 PM | PRELIM EOD signals → SELL signals acted on |
| ~4:05 PM | FINAL EOD signals via `after_market_closes()` (5-min delay for price settling) |
| Overnight | ORB: direct-trade LONG positions held until SELL signal |
| Overnight | SWING: all positions held (subject to cooldown rules) |

---

## Setup

### Prerequisites

- **Python 3.12** — LumiBot's `numba` dependency requires < 3.14
- **[Ollama](https://ollama.com)** — installed and running locally
- **[Alpaca](https://alpaca.markets)** — two paper or live trading accounts

### Pull the Ollama model

```bash
ollama pull llama3.2:3b
```

### Installation

```bash
git clone https://github.com/virtualsheng/trading-bot.git
cd trading-bot

py -3.12 -m venv venv
venv\Scripts\activate        # Windows

pip install --upgrade pip
pip install lumibot pandas numpy python-dotenv yfinance
pip install alpaca-trade-api alpaca-py requests pytz fastapi uvicorn
pip install -e .
```

### Environment variables (.env)

```env
# ── ORB Account (day trade, swing_mode=false) ──────────────────────────────
ALPACA_API_KEY_ORB=your_orb_key
ALPACA_API_SECRET_ORB=your_orb_secret
ALPACA_IS_PAPER_ORB=true

# ── SWING Account (overnight, swing_mode=true) ─────────────────────────────
ALPACA_API_KEY_SWING=your_swing_key
ALPACA_API_SECRET_SWING=your_swing_secret
ALPACA_IS_PAPER_SWING=true

# ── Notifications (optional — leave blank to disable) ──────────────────────
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
EMAIL_SENDER=your_email@gmail.com
EMAIL_PASSWORD=your_app_password
EMAIL_RECIPIENT=your_email@gmail.com

DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## Configuration

All strategy parameters are set in `PARAMS` inside `runners/run_live_combined.py`:

| Parameter | Default | Description |
|---|---|---|
| `sleeptime_orb` | `"2M"` | Iteration speed during 9:45 AM–noon ORB window |
| `sleeptime_default` | `"5M"` | Iteration speed outside ORB window |
| `after_close_delay_minutes` | `5` | Minutes to wait after close before FINAL signals |
| `risk_pct` | `0.01` | Base risk per trade (1% of portfolio) |
| `reward_ratio` | `2.0` | Target = stop distance × reward_ratio |
| `max_positions` | `8` | Max simultaneous open positions |
| `ai_min_confidence` | `0.55` | Minimum AI confidence to enter |
| `min_stop_pct` | `0.005` | Minimum stop distance (0.5% of price) |
| `max_position_pct` | `0.15` | Maximum single position (15% of portfolio) |
| `min_breakout_pct` | `0.001` | Minimum breakout beyond OR boundary (0.1%) |
| `eod_exit_time` | `"15:45"` | Time to close leveraged/inverse ETFs |
| `swing_mode` | `False` / `True` | Set per account automatically |
| `swing_min_conviction` | `75` | Minimum conviction score to enter in swing mode |
| `swing_sell_cooldown_days` | `90` | Days between selling the same symbol (swing) |
| `swing_force_sell_conviction` | `85` | Force-sell conviction threshold override |
| `swing_force_sell_bear_score` | `5` | Force-sell bear score threshold |

---

## Running the Bot

### One-click launcher (recommended)

```bat
start_bot.bat
```

Opens three windows:
1. **Dashboard** (port 5001)
2. **ORB account** — `run_live_combined.py --account orb`
3. **SWING account** — `run_live_combined.py --account swing`

### Manual launch

```bash
# Terminal 1 — Dashboard
python runners/dashboard_server.py

# Terminal 2 — ORB account (day trade)
python runners/run_live_combined.py --account orb

# Terminal 3 — SWING account (overnight)
python runners/run_live_combined.py --account swing
```

### Single account (if you only want one)

```bash
python runners/run_live_combined.py --account orb
```

---

## Notifications

All notifications use the subject prefix `Trade-Bot:` for easy email filtering.

Fires on: trade entry, trade exit (with P&L), EOD signal summary.

| Channel | Setup |
|---|---|
| Email | `SMTP_*` vars in `.env`, Gmail App Password recommended |
| Discord | `DISCORD_WEBHOOK_URL` in `.env` |
| Telegram | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env` |

---

## Symbols

Default `symbols.txt` — 40 symbols across sectors (shared by both accounts):

- **Broad market:** SPY, QQQ, SPMO, QQQM, TQQQ, SQQQ
- **Semiconductors:** SMH, NVDA, MU, TSM, AMAT, LRCX, SNDK, DRAM
- **Precious metals:** GLDM, PSLV, GDXJ, GDMN, GDE, ARIS, AG, PAAS, SLVP
- **Tech/AI:** PLTR, ROBO
- **Crypto:** IBIT
- **Energy/commodities:** DBC, NANR, REMX
- **Space/defense:** UFO, RKLB
- **Uranium:** URA, URNM
- **International:** EWT, EWY, EWJV
- **Financials:** JPM
- **Alternatives:** DBMF, GRID, CEG, GEV

---

## Risk Management

**Position level:**
- Maximum 8 simultaneous open positions per account
- Base risk: 1% of portfolio per trade
- AI confidence scales size 0.5×–2.0× (hard cap: 2% effective risk)
- Minimum stop distance: 0.5% of price
- Maximum position value: 15% of portfolio
- Leveraged/inverse ETFs always closed by 3:45 PM (ORB account)

**Entry guards:**
- Minimum 0.1% breakout beyond OR boundary
- Opposing position check (won't open TQQQ if SQQQ already held)
- Earnings within 48 hours: skip
- Low liquidity regime (≥ 0.70 confidence): skip
- Poor ORB suitability (≥ 0.70 confidence): skip
- AI confidence below 0.55: skip
- One entry per symbol per day
- No new entries after noon

**Bearish trades (ORB account only):**
- Only if symbol has a real inverse ETF
- Always expressed by BUYING the inverse ETF — never short-selling
- SELL signal on direct-trade symbol: skip new entry, close existing long

**Swing account:**
- No leveraged or inverse ETFs — ever
- Only direct-trade stocks (the underlying itself)
- 90-day cooldown per symbol between sells

---

## Known Limitations

- **Single Ollama instance:** Both accounts share one Ollama instance. SWING starts 15 seconds after ORB to avoid startup contention. During operation, regime detection retries up to 3 times with backoff if Ollama is busy.
- **Alpaca market hours guard:** LumiBot stops calling `on_trading_iteration()` after ~4:03 PM. FINAL EOD signals are handled via `after_market_closes()` with a 5-min delay.
- **Python 3.12 only:** `numba` (required by LumiBot) does not support Python 3.13+.

---

## Disclaimer

This software is for educational and research purposes only. It is not financial advice. Trading leveraged ETFs and inverse ETFs carries substantial risk of loss. Paper trade extensively before using real money. Past performance does not guarantee future results.
