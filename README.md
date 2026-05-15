# Trading Bot — 0.1.0v9

An AI-enhanced algorithmic trading system for US equities and leveraged ETFs. Combines momentum-based technical analysis with Opening Range Breakout (ORB) execution, filtered and sized by a local LLM running via Ollama.

---

## Table of Contents

- [Overview](#overview)
- [Trade Model](#trade-model)
- [Architecture](#architecture)
- [Signal Pipeline](#signal-pipeline)
- [AI Layer](#ai-layer)
- [Pre-Market Enrichment](#pre-market-enrichment)
- [Sentiment-Trading-Alpha Integration](#sentiment-trading-alpha-integration)
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
Scans all symbols in `symbols.txt` using EMA crossovers (2/3/5), RSI(14), MACD, SMA50/200, and volume ratio to generate a directional bias per symbol: `BUY`, `STRONG_BUY`, `SELL`, `STRONG_SELL`, or `HOLD`. Results are written to `cache/daily_bias.json`. A preliminary pass runs at 3:50 PM while the market is still technically open, and a final confirmed pass runs via `after_market_closes()` at approximately 4:05 PM using official closing prices.

**Stage 2 — Morning execution (9:45 AM – noon ET, 2-min iterations)**
Watches for price to break above the Opening Range High (for bullish setups) or below the Opening Range Low (for bearish setups). Only executes breakouts aligned with the prior-day bias, then routes the order to the appropriate leveraged ETF. Before any order is placed, a local LLM grades the setup quality and the current market regime adjusts position sizing and stop/target levels.

---

## Trade Model

**We never short-sell. Every order submitted is a BUY.**

| Signal | Breakout direction | Execution |
|---|---|---|
| BUY / STRONG_BUY | Price > OR High + 0.1% | BUY bull leveraged ETF (e.g. QQQ → TQQQ) |
| SELL / STRONG_SELL | Price < OR Low − 0.1% | BUY inverse ETF (e.g. IBIT → BITI) |
| SELL / STRONG_SELL | No inverse ETF available | Skip — cannot express bearish view |
| HOLD | Price > OR High + 0.1% | BUY bull ETF at 0.5× size |
| HOLD | Inside range or breakdown | Skip — no trade on HOLD + no upside break |

For inverse ETF positions:
- We BUY the inverse ETF and hold it as a LONG position
- Stop is below our entry price, target is above (we want the inverse ETF to go up)
- Position closes at EOD (leveraged/inverse ETFs are never held overnight)
- Position also closes if the underlying symbol later gets a BUY signal (the thesis reversed)

For bull ETF and direct-trade positions:
- Direct-trade symbols (no leveraged pair) can be held overnight
- Position closes when the underlying symbol gets a SELL signal, or stop/target is hit

---

## Architecture

```
symbols.txt
    │
    ▼
signal_engine.py          ← EMA/RSI/MACD/SMA technical scan (EOD)
    │
    ▼
daily_bias.json           ← BUY / SELL / HOLD per symbol (persisted)
    │
    ├── premarket_signals.py  ← Gap analysis + Alpaca News + Sentiment-Trading-Alpha
    │
    ▼
trend_filtered_orb.py     ← Main strategy (LumiBot)
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
- **RSI(14)** — momentum extremes (overbought/oversold)
- **MACD histogram** — trend confirmation
- **SMA 50 / SMA 200** — trend context
- **Volume ratio** — above-average volume confirms breakouts

Bull score and bear score (0–6 each) determine the signal:

| Condition | Signal |
|---|---|
| bull_score ≥ 5, RSI < 68, vol_ratio > 1.1 | STRONG_BUY |
| bull_score ≥ 4, RSI < 62 | BUY |
| bear_score ≥ 5, RSI > 32, vol_ratio > 1.1 | STRONG_SELL |
| bear_score ≥ 4, RSI > 38 | SELL |
| Otherwise | HOLD |

### ORB Execution (trend_filtered_orb.py)

The Opening Range is established from the first three 5-minute bars (9:30–9:45 AM). Entry is triggered when:

1. Prior-day bias is BUY/STRONG_BUY and price breaks above OR High × 1.001
2. Prior-day bias is SELL/STRONG_SELL and price breaks below OR Low × 0.999
3. Prior-day bias is HOLD and price breaks above OR High × 1.001 (half size)

A 0.1% minimum breakout filter (`min_breakout_pct`) prevents entries on noise right at the OR boundary.

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
| < 0.55 | Skip (do not enter) |

If Ollama is unavailable, defaults to 0.60 confidence / 0.5× size so trades still execute at reduced risk.

### Regime Detector (detect_regime)

Every 30 minutes, and at market open, the regime is classified using 5 bars each of 5m/15m/1H data plus RSI and ATR. Possible regimes: `trending_up`, `trending_down`, `ranging`, `volatile`, `mean_reversion`, `low_liquidity`.

- **volatile** regime: size reduced to 0.75×
- **low_liquidity** regime (≥ 0.70 confidence): symbol skipped entirely
- **poor ORB suitability** (≥ 0.70 confidence): symbol skipped
- Regime also adjusts stop and target distances via `stop_adjustment` and `target_adjustment` multipliers

### Trade Narrator (narrate_trade)

After a position closes, Ollama writes a 2-3 sentence journal entry analyzing what worked and what could be improved. Stored in the SQLite trade journal.

---

## Pre-Market Enrichment

`strategies/premarket_signals.py` enriches the bias before the ORB window opens:

**1. Gap Analysis (Alpaca Data API)**
Compares pre-market quote to prior close. Gaps ≥ 0.5% are tagged `GAP_UP` or `GAP_DOWN`. Aligned gaps boost conviction; misaligned gaps reduce it.

**2. Alpaca News Sentiment**
Scans last 24h of headlines for each symbol. Positive/negative word scoring adjusts conviction.

**3. Sentiment-Trading-Alpha (macro signal)**
Calls `POST /api/v1/analyze` with only `["SPY", "QQQ"]` — the two symbols STA natively supports. Returns a portfolio-level `LONG`/`SHORT`/`HOLD` signal with confidence and conviction level. Applied to all symbols as a macro market-direction modifier. Runs in a background daemon thread at startup — never blocks trading.

---

## Sentiment-Trading-Alpha Integration

**Repo:** `techjeffe/Sentiment-Trading-Alpha` (runs locally)

**Why only SPY + QQQ:** STA's validator only accepts symbols registered in its config (`DEFAULT_TRACKED_SYMBOLS = ["USO", "IBIT", "QQQ", "SPY"]`). Custom symbols trigger a 400 error. We use STA for what it does best: macro market direction.

**Endpoint:** `POST http://localhost:8000/api/v1/analyze`

**Auth:** `X-Admin-Token` header (set `SENTIMENT_ADMIN_TOKEN` in `.env`)

**Request:**
```json
{
  "symbols": ["SPY", "QQQ"],
  "max_posts": 20,
  "include_backtest": false,
  "lookback_days": 14
}
```

**Timeout:** 600s client-side, 900s STA-internal (`ANALYSIS_TIMEOUT_SECONDS=900` set in `start_bot.bat`). The default STA timeout of 420s is too short for `0xroyce/plutus:latest` on first run after a cold start.

**Known behavior:** First call after STA starts generates LLM keywords for each symbol (~50s/symbol). SPY and QQQ are pre-cached in STA's SQLite after the first run, so subsequent calls are fast (~10-20s total).

---

## Earnings Filter

`strategies/earnings_filter.py` fetches the next earnings date from Yahoo Finance for each symbol and blocks new entries within 48 hours of a scheduled report. Falls open on network failure (never silently blocks all trades). ETFs and macro symbols log "no earnings found" at DEBUG level — this is expected, not an error.

---

## Trade Journal

Every trade is logged to `cache/trade_journal.db` (SQLite) with:

- Entry/exit price, quantity, P&L, R-multiple
- OR High, OR Low, OR Mid at time of entry
- Signal action, bull/bear score, RSI, volume ratio
- AI confidence, size multiplier, flags, volume quality
- Market regime, ORB suitability, stop/target adjustments
- Ollama-generated narrative (post-trade)
- Sentiment fields pre-built for future integration

---

## Dashboard

FastAPI server on port 5001 (`runners/dashboard_server.py`). Serves `dashboard.html` with live position view, recent trades, regime status, and signal summary.

---

## Project Structure

```
trading-bot/
│
├── strategies/
│   ├── ai_engine.py             # Ollama: setup grader, regime detector, narrator
│   ├── earnings_filter.py       # Earnings calendar filter (Yahoo Finance)
│   ├── leverage_map.py          # Symbol → leveraged ETF pair registry
│   ├── premarket_signals.py     # Gap, news, Sentiment-Trading-Alpha enrichment
│   ├── signal_engine.py         # EMA/RSI/MACD/SMA technical signal generator
│   ├── trade_journal.py         # SQLite trade journaling and stats
│   └── trend_filtered_orb.py    # ★ Main live strategy (full pipeline)
│
├── runners/
│   ├── dashboard_server.py      # FastAPI dashboard (port 5001)
│   ├── run_live_combined.py     # ★ Main live runner — starts TrendFilteredORB
│   └── run_backtest_combined.py # Backtest runner (Polygon intraday data)
│
├── notifications/
│   ├── discord.py               # Discord webhook
│   ├── emailer.py               # SMTP email
│   └── telegram.py              # Telegram bot
│
├── cache/
│   ├── daily_bias.json          # Prior-day signal cache (overwritten daily)
│   └── trade_journal.db         # SQLite trade database
│
├── logs/
│   └── bot_YYYYMMDD_HHMMSS.log  # Timestamped log per session
│
├── symbols.txt                  # Master symbol list (one ticker per line)
├── start_bot.bat                # One-click Windows launcher (3 processes)
├── setup.py                     # pip install -e .
├── requirements.txt             # Python dependencies
└── .env                         # API keys — never commit
```

---

## Leveraged ETF Map

`strategies/leverage_map.py` maps each signal symbol to its highest-available leveraged ETF pair. Bull ETF is bought on BUY signals; bear/inverse ETF is bought on SELL signals.

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
| UFO / RKLB / URA / URNM / EWT / EWJV / EWY / DBMF / GRID / CEG / REMX / GEV | direct | direct | 1× | No leveraged pair — trade underlying |

Symbols with `bull == bear` (direct-trade) cannot express a bearish view. SELL signals on these symbols are silently skipped for new entries, but existing long positions are still closed when the signal flips to SELL.

---

## Daily Schedule

| Time (ET) | Event |
|---|---|
| Script start | Ollama warmup, bias refresh, earnings cache, STA background thread |
| ~9:00 AM | `before_market_opens()`: earnings cache cleared, regime pre-warmed, gap/news enrichment |
| 9:30 AM | Position sync from Alpaca broker |
| 9:45 AM | ORB entry window opens — iterations switch from 5-min to **2-min** |
| 9:45 AM – noon | ORB entries evaluated every 2 minutes |
| Noon | ORB entry window closes — iterations switch back to 5-min |
| Every 30 min | Regime detection refresh (QQQ) |
| 3:45 PM | Leveraged and inverse ETFs closed |
| 3:50 PM | PRELIM EOD signals (near-close prices) — SELL signals acted on |
| ~4:05 PM | FINAL EOD signals via `after_market_closes()` (5-min delay for price settling) |
| Overnight | Direct-trade LONG positions held until SELL signal next session |

---

## Setup

### Prerequisites

- **Python 3.12** — LumiBot's `numba` dependency requires < 3.14
- **[Ollama](https://ollama.com)** — installed and running locally
- **[Alpaca](https://alpaca.markets)** — paper or live trading account
- **[Sentiment-Trading-Alpha](https://github.com/techjeffe/Sentiment-Trading-Alpha)** — cloned locally and running

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
# Alpaca
ALPACA_API_KEY=your_key
ALPACA_API_SECRET=your_secret
ALPACA_IS_PAPER=true

# Sentiment-Trading-Alpha
SENTIMENT_API_URL=http://localhost:8000
SENTIMENT_ADMIN_TOKEN=your_sta_token

# Optional
SWING_MODE=false
```

---

## Configuration

All strategy parameters are set in `PARAMS` inside `runners/run_live_combined.py`:

| Parameter | Default | Description |
|---|---|---|
| `sleeptime_orb` | `"2M"` | Iteration speed during 9:45 AM–noon ORB window |
| `sleeptime_default` | `"5M"` | Iteration speed outside ORB window |
| `after_close_delay_minutes` | `5` | Minutes to wait after close before running FINAL signals |
| `risk_pct` | `0.01` | Base risk per trade (1% of portfolio) |
| `reward_ratio` | `2.0` | Target = stop distance × reward_ratio |
| `max_positions` | `8` | Max simultaneous open positions |
| `ai_min_confidence` | `0.55` | Minimum AI confidence to enter |
| `min_stop_pct` | `0.005` | Minimum stop distance (0.5% of price) |
| `max_position_pct` | `0.15` | Maximum single position (15% of portfolio) |
| `min_breakout_pct` | `0.001` | Minimum breakout beyond OR boundary (0.1%) |
| `eod_exit_time` | `"15:45"` | Time to close leveraged/inverse ETFs |
| `hold_override` | `False` | If True, HOLD-bias entries are enabled |
| `hold_override_size` | `0.5` | Size multiplier for HOLD-bias entries |
| `swing_mode` | `False` | Hold direct-trade positions long-term (set via `SWING_MODE` env var) |

---

## Running the Bot

### One-click launcher (recommended)

```bat
start_bot.bat
```

Opens three windows:
1. **Sentiment-Trading-Alpha backend** (port 8000) — `ANALYSIS_TIMEOUT_SECONDS=900` pre-set
2. **Dashboard** (port 5001)
3. **Trading bot**

### Manual launch

```bash
# Terminal 1 — STA backend
cd C:\Users\sheng\Documents\Sentiment-Trading-Alpha
set ADMIN_API_TOKEN=your_token
set ANALYSIS_TIMEOUT_SECONDS=900
python run.py

# Terminal 2 — Dashboard
python runners/dashboard_server.py

# Terminal 3 — Trading bot
python runners/run_live_combined.py
```

### Swing mode

```bat
set SWING_MODE=true
python runners/run_live_combined.py
```

In swing mode, direct-trade long positions are held until a SELL signal meets the conviction threshold, with a 90-day cooldown between sells per symbol. A force-sell override fires if conviction ≥ 85, bear_score ≥ 5, and the signal is STRONG_SELL.

---

## Notifications

Fires on: trade entry, trade exit (with P&L), EOD signal summary.

| Channel | Setup |
|---|---|
| Email | `SMTP_*` vars in `.env`, Gmail App Password recommended |
| Discord | `DISCORD_WEBHOOK_URL` in `.env` |
| Telegram | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env` |

---

## Symbols

Default `symbols.txt` — 40 symbols across sectors:

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
- Maximum 8 simultaneous open positions
- Base risk: 1% of portfolio per trade
- AI confidence scales size 0.5×–2.0× (hard cap: 2% effective risk)
- Minimum stop distance: 0.5% of price (prevents oversized qty on tight ORs)
- Maximum position value: 15% of portfolio (prevents over-concentration)
- Leveraged/inverse ETFs always closed by 3:45 PM
- Direct-trade symbols held overnight, closed on SELL signal

**Entry guards:**
- Minimum 0.1% breakout beyond OR boundary (filters noise at the edge)
- Opposing position check (won't open TQQQ if SQQQ already held, and vice versa)
- Earnings within 48 hours: skip
- Low liquidity regime (≥ 0.70 confidence): skip
- Poor ORB suitability (≥ 0.70 confidence): skip
- AI confidence below 0.55: skip
- One entry per symbol per day (ORB state resets overnight)
- No new entries after noon

**Bearish trades:**
- Only possible if symbol has a real inverse ETF (bull ≠ bear in leverage_map)
- Always expressed by BUYING the inverse ETF — never short-selling
- SELL signal on direct-trade symbol (RKLB, URA etc.): skip new entry, but close existing long

---

## Known Limitations

- **Sentiment-Trading-Alpha cold start:** First call after STA restarts generates LLM keywords for SPY and QQQ, which can take 2-5 minutes. This runs in a background thread and never blocks trading. Subsequent calls are fast once the keyword cache is warm.
- **Single Ollama instance:** The trading bot and STA share one Ollama instance. STA's `0xroyce/plutus:latest` (5.7GB) calls can cause Ollama to be temporarily unavailable for regime detection. Regime detection retries up to 3 times with backoff and falls back to neutral if all attempts fail.
- **Alpaca market hours guard:** LumiBot stops calling `on_trading_iteration()` after ~4:03 PM. FINAL EOD signals are handled via `after_market_closes()` (with a 5-min delay) to work around this.
- **Python 3.12 only:** `numba` (required by LumiBot) does not support Python 3.13+. Use exactly `py -3.12`.

---

## Disclaimer

This software is for educational and research purposes only. It is not financial advice. Trading leveraged ETFs and inverse ETFs carries substantial risk of loss. Paper trade extensively before using real money. Past performance of any strategy does not guarantee future results.