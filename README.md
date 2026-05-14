# Trading Bot

An AI-enhanced algorithmic trading system for US equities and leveraged ETFs. Combines momentum-based technical analysis with Opening Range Breakout (ORB) execution, filtered and sized by a local LLM running via Ollama. No cloud AI costs, no data leaving your machine.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Signal Pipeline](#signal-pipeline)
- [AI Layer](#ai-layer)
- [Regime-Based Strategy Switching](#regime-based-strategy-switching)
- [Mean Reversion Strategy](#mean-reversion-strategy)
- [Pre-Market Enrichment](#pre-market-enrichment)
- [Signal Combiner](#signal-combiner)
- [Earnings Calendar Filter](#earnings-calendar-filter)
- [Trade Journal](#trade-journal)
- [Dashboard](#dashboard)
- [Project Structure](#project-structure)
- [Leveraged ETF Map](#leveraged-etf-map)
- [Setup](#setup)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)
- [Alert Scripts](#alert-scripts)
- [Backtesting](#backtesting)
- [Notifications](#notifications)
- [Symbols](#symbols)
- [Risk Management](#risk-management)
- [Future Enhancements](#future-enhancements)
- [Disclaimer](#disclaimer)

---

## Overview

This bot implements a two-stage trading approach:

**Stage 1 — Afternoon technical signal (3:50 PM ET)**
Scans all symbols in `symbols.txt` using EMA crossovers, RSI, MACD, and SMA200 to generate a directional bias (BUY / SELL / HOLD) for the next session. Results are persisted to `cache/daily_bias.json`. A second final pass runs at 4:15 PM using official closing prices, overwriting the preliminary cache with accurate end-of-day data.

**Stage 2 — Morning execution (9:45 AM ET onward)**
Waits for price to break above or below the first 15 minutes of trading. Only executes breakouts that align with the prior-day bias, then routes the trade to the highest-leverage ETF available for that signal symbol.

Before any order is placed, a local LLM (Ollama) grades the setup quality (0.0–1.0 confidence), classifies the market regime, and determines whether to use a momentum or mean-reversion approach. Position size scales dynamically with confidence.

Every trade is recorded to a SQLite journal with full context for later analysis and ML training.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│              STARTUP (script launch)                    │
│  Ollama warmup → model loaded into memory               │
│  Bias cache loaded from cache/daily_bias.json           │
│  Earnings cache pre-fetched for all symbols             │
│  Sentiment Trading Alpha pipeline triggered (async)     │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│           SIGNAL LAYER (3:50 PM + 4:15 PM ET)           │
│  symbols.txt → signal_engine.py                         │
│  EMA 2/3/5 crossover + RSI + MACD + SMA50/200           │
│  → BUY / SELL / HOLD per symbol                         │
│  3:50 PM → preliminary cache (prelim close prices)      │
│  4:15 PM → final cache (official closing prices)        │
│  → cache/daily_bias.json (persisted overnight)          │
└──────────────────────────┬──────────────────────────────┘
                           │ prior-day bias
┌──────────────────────────▼──────────────────────────────┐
│     PRE-MARKET ENRICHMENT (~9:00 AM ET)                 │
│  Gap analysis (Alpaca pre-market bars)                  │
│  Alpaca News sentiment (keyword scoring, last 24h)      │
│  Sentiment Trading Alpha (cached from async startup)    │
│  → Enriched bias with gap_pct, news_sentiment,          │
│    sentiment_signal, conviction boost score             │
└──────────────────────────┬──────────────────────────────┘
                           │ enriched bias
┌──────────────────────────▼──────────────────────────────┐
│           EXECUTION LAYER (9:45 AM – noon)              │
│  Morning ORB breakout detected                          │
│  Bias check: does breakout align with prior-day signal? │
│  Earnings filter: report within 48h? → skip             │
│  leverage_map.py → route to highest-leverage ETF pair   │
│  Candidates ranked by conviction score                  │
│  Top N candidates executed (N = max_positions - open)   │
└──────────────────────────┬──────────────────────────────┘
                           │ candidate trade
┌──────────────────────────▼──────────────────────────────┐
│                AI FUSION LAYER (Ollama)                 │
│  Regime detect → trending / ranging / volatile /        │
│                  mean_reversion / low_liquidity         │
│       │                                                 │
│       ├─ Trending   → ORB momentum entry                │
│       ├─ Ranging    → Mean-reversion fade entry         │
│       ├─ Volatile   → ORB entry at 0.75x size           │
│       └─ Low liquid → Skip entirely                     │
│                                                         │
│  Setup grader → confidence 0.0–1.0 → size multiplier   │
└──────────────────────────┬──────────────────────────────┘
                           │ sized order
┌──────────────────────────▼──────────────────────────────┐
│            EXECUTION & LOGGING                          │
│  Alpaca API (paper or live)                             │
│  trade_journal.py → SQLite (cache/trade_journal.db)     │
│  Notifications → Email / Discord / Telegram             │
│  Leveraged ETFs closed at 3:45 PM                       │
│  Direct-trade symbols held overnight until SELL signal  │
│  3:50 PM SELL signals acted on immediately              │
│  4:15 PM SELL signals acted on from official close      │
└─────────────────────────────────────────────────────────┘
```

---

## Signal Pipeline

### Technical Signal Engine (`strategies/signal_engine.py`)

Runs on 400 days of daily bars from Alpaca. Computes:

| Indicator | Purpose |
|---|---|
| EMA 2/3/5 crossover | Short-term momentum direction |
| SMA 50 / SMA 200 | Trend regime filter |
| RSI(14) | Overbought/oversold confirmation |
| MACD histogram | Momentum direction confirmation |
| Volume ratio (20-day avg) | Conviction filter |

**Signal outputs:**

| Action | Criteria |
|---|---|
| `STRONG_BUY` | Bull score ≥ 5, RSI < 68, volume > 1.1× avg |
| `BUY` | Bull score ≥ 4, RSI < 62 |
| `STRONG_SELL` | Bear score ≥ 5, RSI > 32, volume > 1.1× avg |
| `SELL` | Bear score ≥ 4, RSI > 38 |
| `HOLD` | No clear signal |

### Opening Range Breakout (`core/orb.py`, `strategies/orb_strategy.py`)

- Opening range: first 15 minutes (9:30–9:45 AM ET) using 5-minute bars
- Entry: 5-minute close above OR high (bullish) or below OR low (bearish)
- Stop loss: midpoint of the opening range
- Profit target: 2× the risk distance (configurable)
- EOD exit: leveraged ETFs always closed by 3:45 PM ET
- Overnight: direct-trade (non-leveraged) symbols held until SELL signal

### EOD Signal Timing (v5)

| Time | Action |
|---|---|
| 3:50 PM ET | Preliminary signals run — SELL signals acted on immediately |
| 4:15 PM ET | Final signals run using official close prices — overwrites preliminary cache, SELL signals acted on |

---

## AI Layer

All AI runs locally via **Ollama** on `localhost:11434`. No API keys, no per-trade cost, no data leaving your machine. Default model: `qwen3:8b`.

### Startup Warmup

`check_ollama_available()` is called in `initialize()` the moment the strategy starts — not at market open. It verifies Ollama is running, confirms the model exists, and sends a trivial warmup prompt so the model is loaded into memory before the first trade fires at 9:45 AM. If Ollama is unreachable, trades still execute at 0.5× size using fallback confidence 0.60.

### Setup Grader

Before each trade executes, the last 25 five-minute candles are passed to Ollama with a structured prompt evaluating:

- Volume quality relative to morning average
- Price coiling tightness before breakout
- Breakout extension (overextended = risky)
- Candle body strength at the moment of breakout
- Successive momentum bars in the breakout direction

Returns a **confidence score (0.0–1.0)** mapped to position size:

| Confidence | Size Multiplier | Effective Risk |
|---|---|---|
| ≥ 0.90 (Elite) | 2.0× | 2.0% of portfolio |
| ≥ 0.75 (Strong) | 1.5× | 1.5% |
| ≥ 0.65 (Normal) | 1.0× | 1.0% |
| ≥ 0.55 (Weak) | 0.5× | 0.5% |
| < 0.55 | Skip | Trade not taken |
| AI unavailable | 0.5× fallback | 0.5% |

### Trade Narrator

After every position closes, `narrate_trade()` generates a 2–3 sentence plain-English journal entry describing what worked and what could be improved. Stored in the SQLite trade journal as `ai_narrative`.

---

## Regime-Based Strategy Switching

Every 30 minutes during market hours, `detect_regime()` sends multi-timeframe bar data (5m, 15m, 1h) for QQQ to Ollama and receives a regime classification. The regime is also pre-warmed in `before_market_opens()` so the first trade of the day has a reading immediately.

| Regime | ORB Behavior | Mean-Reversion |
|---|---|---|
| `trending_up` | Normal ORB entry | No |
| `trending_down` | Normal ORB entry | No |
| `ranging` | Skipped | Yes — fade the breakout |
| `mean_reversion` | Skipped | Yes — fade the breakout |
| `volatile` | ORB at 0.75× size | No |
| `low_liquidity` | Skipped entirely | No |

---

## Mean Reversion Strategy

When the regime is `ranging` or `mean_reversion` with confidence ≥ 0.70:

- Instead of entering with the breakout, the bot fades it
- Entry condition: price has broken the OR boundary by ≥ 0.3% AND RSI confirms overextension (>68 for short fades, <32 for long fades)
- Stop: OR boundary + 1× ATR beyond entry
- Target: OR midpoint (mean reversion target)
- Minimum 1:1 reward:risk required
- Sized at 0.75× normal risk (more uncertain than momentum)

---

## Pre-Market Enrichment

Runs at ~9:00–9:15 AM ET via `strategies/premarket_signals.py`. Three signal sources enrich the daily bias before the ORB window opens:

### 1. Gap Analysis
- Fetches pre-market price vs prior close via Alpaca Data API
- Computes `gap_pct`, `gap_vol_ratio`, `gap_signal` (GAP_UP / GAP_DOWN / FLAT)
- ≥ 0.5% gap = directional signal; volume ratio amplifies conviction

### 2. Alpaca News Sentiment
- Pulls last 24h headlines from Alpaca News API (free with any Alpaca account)
- Keyword scoring (positive/negative word lists) → sentiment score −1.0 to +1.0
- Adds `news_sentiment` and `news_headline_count` to bias

### 3. Sentiment Trading Alpha
- Optional integration with [Jeff's Sentiment Trading Alpha](https://github.com/techjeffe/Sentiment-Trading-Alpha)
- Triggered as background thread at startup — result cached by 9:45 AM
- Adds `sentiment_signal` (LONG/SHORT/HOLD), `sentiment_confidence`, `sentiment_conviction`
- Fails silently if server is offline — bot continues without it

### Conviction Boost
`premarket_conviction_boost()` scores up to +25 points when gap, news, and sentiment all align with the trade direction. Negative gap (working against trade) subtracts points.

---

## Signal Combiner

`strategies/signal_combiner.py` fuses technical and sentiment signals:

| Agreement | Result |
|---|---|
| `CONFIRMED` | Both agree — boosted confidence |
| `TECHNICAL_ONLY` | Sentiment neutral — technical leads |
| `SENTIMENT_ONLY` | Technicals neutral — sentiment leads |
| `CONFLICT` | Directional disagreement — trade skipped |

---

## Earnings Calendar Filter

`strategies/earnings_filter.py` uses Yahoo Finance (free, no API key) to check the next earnings date for each symbol before entry.

- Blocks new positions within **48 hours** of a scheduled earnings report
- Results cached per symbol per day — only one Yahoo Finance call per symbol per session
- Cache cleared each morning in `before_market_opens()`
- Pre-fetched for all symbols at startup to avoid latency during the ORB window
- **Fails open**: if the calendar cannot be fetched, the trade proceeds

---

## Trade Journal

Every trade is recorded to **`cache/trade_journal.db`** (SQLite). Schema captures:

| Category | Fields |
|---|---|
| Identity | date, symbol, exec ticker, direction |
| Signal | action, source, bull/bear score, RSI, volume ratio |
| Entry | time, price, quantity, OR levels, breakout extension % |
| AI Grading | confidence, size multiplier, flags, volume quality, PA quality |
| Regime | regime type, confidence, ORB suitability, stop/target adjustments |
| Risk | initial stop, initial target, risk %, planned R |
| Exit | time, price, reason (STOP / TARGET / EOD / SELL_SIGNAL / MANUAL) |
| Outcome | P&L, P&L %, R-multiple, WIN / LOSS / BREAKEVEN |
| Context | VIX level hook, SPY trend, sentiment score, sentiment signal |
| Narrative | AI-generated plain-English journal entry |

**View performance stats:**
```python
from strategies.trade_journal import TradeJournal
stats = TradeJournal().get_stats(days=30)
# Returns: win rate, P&L, profit factor, avg R, by regime/symbol/AI tier
```

**Export to CSV:**
```python
TradeJournal().export_csv("my_trades.csv")
```

Browse the database visually with [DB Browser for SQLite](https://sqlitebrowser.org/) (free).

---

## Dashboard

A real-time web dashboard runs alongside the bot on `http://localhost:5001`.

**Pages:**
- **Overview** — Bias summary, open positions, equity curve, recent trades
- **Signals** — Full bias table with gap %, news sentiment, Sentiment Alpha columns, filterable by BUY/SELL/HOLD
- **Positions** — Live positions from Alpaca with stop, target, AI confidence, regime
- **Trade Log** — Full history filterable by result (WIN/LOSS) and direction (LONG/SHORT)
- **Performance** — Stats by period (7d/30d/60d/90d/1y), by symbol, by regime, daily P&L chart
- **Regime** — Latest regime readings per symbol with confidence and reasoning

Launch via `start_bot.bat` — opens both the bot and dashboard automatically.

---

## Project Structure

```
trading-bot/
│
├── strategies/                      # LumiBot strategy classes and signal logic
│   ├── ai_engine.py                 # Ollama: setup grader, regime detector, narrator
│   ├── earnings_filter.py           # Earnings calendar filter (Yahoo Finance)
│   ├── ema_crossover_strategy.py    # Daily EMA crossover strategy (LumiBot)
│   ├── leverage_map.py              # Symbol → leveraged ETF pair registry
│   ├── mean_reversion_strategy.py   # Mean-reversion fade logic for ranging regimes
│   ├── orb_strategy.py              # Base ORB strategy (LumiBot, backtest + live)
│   ├── premarket_signals.py         # Gap analysis, Alpaca News, Sentiment Alpha
│   ├── signal_combiner.py           # Combines technical + sentiment signals
│   ├── signal_engine.py             # EMA/RSI/MACD technical signal generator
│   ├── trade_journal.py             # SQLite trade journaling and stats
│   └── trend_filtered_orb.py        # ★ Main live strategy (full pipeline)
│
├── core/                            # Shared data and indicator utilities
│   ├── data.py                      # Alpaca + yfinance data fetcher
│   ├── indicators.py                # EMA, RSI, MACD implementations
│   └── orb.py                       # ORB signal for standalone alert scripts
│
├── runners/                         # Entry points for backtesting and live trading
│   ├── dashboard_server.py          # FastAPI dashboard server (port 5001)
│   ├── run_backtest_combined.py     # ORB backtest with Polygon intraday data
│   ├── run_backtest_ema.py          # EMA crossover backtest (Yahoo daily data)
│   ├── run_backtest_orb.py          # ORB backtest (Yahoo, ~30 day limit)
│   ├── run_live_combined.py         # ★ Main live runner — starts TrendFilteredORB
│   ├── run_live_ema.py              # EMA strategy live runner
│   ├── run_live_orb.py              # ORB-only live runner
│   └── clear_backtest_trades.py     # Utility: remove backtest artifacts from journal
│
├── alerts/                          # Standalone daily alert scripts
│   ├── run_orb_check.py             # 9:45 AM — morning ORB breakout report
│   └── run_technical_signals.py     # 3:50 PM — EOD signal scan + bias cache write
│
├── notifications/                   # Notification channel adapters
│   ├── discord.py                   # Discord webhook
│   ├── emailer.py                   # SMTP email
│   └── telegram.py                  # Telegram bot
│
├── cache/                           # Runtime data (gitignored)
│   ├── daily_bias.json              # Prior-day signal cache
│   ├── trade_journal.db             # SQLite trade database
│   └── *.csv                        # Polygon intraday data cache (backtesting)
│
├── logs/                            # Signal and execution logs (gitignored)
│   └── daily_signals.log
│
├── dashboard.html                   # Dashboard frontend (served by dashboard_server.py)
├── symbols.txt                      # Master symbol list (one ticker per line)
├── start_bot.bat                    # One-click Windows launcher
├── setup.py                         # Package installation (pip install -e .)
├── requirements.txt                 # Python dependencies
└── .env                             # API keys — never commit this file
```

---

## Leveraged ETF Map

`strategies/leverage_map.py` maps each signal symbol to its highest-available leveraged ETF pair.

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
| IBIT | BITX | — | 2× | Bitcoin |
| JPM | FAS | FAZ | 3× | Financials |
| NANR | ERX | ERY | 2× | Energy/natural resources |
| UFO / RKLB / URA / URNM / EWT / EWJV / DBMF / GRID / CEG / REMX / DBC | direct | direct | 1× | No quality leveraged pair |

---

## Setup

### Prerequisites

- **Python 3.12** (not 3.13 or 3.14 — LumiBot dependency `numba` requires < 3.14)
- **[Ollama](https://ollama.com)** installed and running locally
- **[Alpaca](https://alpaca.markets)** paper trading account
- **[Polygon.io](https://polygon.io)** free API key (intraday backtesting only)

### Installation

```bash
git clone https://github.com/virtualsheng/trading-bot.git
cd trading-bot

# Create virtual environment with Python 3.12
py -3.12 -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux

# Install dependencies
pip install --upgrade pip
pip install lumibot pandas numpy python-dotenv yfinance pandas-ta
pip install alpaca-trade-api alpaca-py requests polygon-api-client pytz
pip install fastapi uvicorn   # Required for dashboard

# Install project as editable package (fixes all import paths)
pip install -e .

# Pull the Ollama model
ollama pull qwen3:8b
```

### Environment Variables

Create `.env` in the project root (use `example.env` as a template). **Never commit `.env`.**

```env
# Alpaca — set ALPACA_IS_PAPER=false for live trading
ALPACA_API_KEY=your_api_key_here
ALPACA_API_SECRET=your_secret_key_here
ALPACA_IS_PAPER=true

# Polygon.io — only needed for intraday backtesting
POLYGON_API_KEY=your_polygon_key_here

# Sentiment Trading Alpha (optional)
SENTIMENT_API_URL=http://localhost:8000
SENTIMENT_ADMIN_TOKEN=your_token_here

# Email notifications (optional)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
EMAIL_SENDER=your_email@gmail.com
EMAIL_PASSWORD=your_app_password
EMAIL_RECIPIENT=your_email@gmail.com

# Discord notifications (optional)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Telegram notifications (optional)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## Configuration

### Symbol List (`symbols.txt`)

One ticker per line. Lines starting with `#` are ignored.

### Strategy Parameters (`runners/run_live_combined.py`)

```python
PARAMS = {
    "orb_minutes":              15,     # Opening range window in minutes
    "bar_minutes":              5,      # Bar size
    "risk_pct":                 0.01,   # Base risk per trade (1% of portfolio)
    "reward_ratio":             2.0,    # 2:1 reward:risk target
    "eod_exit_time":            "15:45",
    "max_positions":            8,      # Max simultaneous open positions
    "ai_min_confidence":        0.55,   # Skip trades below this AI score
    "hold_override":            False,  # Trade HOLD-bias symbols (half size)
    "hold_override_size":       0.5,    # Size multiplier for HOLD-bias trades
    "earnings_filter_enabled":  True,
    "earnings_buffer_hours":    48,
    "regime_switching_enabled": True,
    "mean_reversion_min_conf":  0.70,
}
```

### Ollama Model (`strategies/ai_engine.py`)

```python
OLLAMA_MODEL = "qwen3:8b"   # Change to llama3.2:3b for faster but lighter output
TIMEOUT      = 15            # Seconds before falling back to defaults
```

---

## Running the Bot

### Quick Start (Windows)

Double-click **`start_bot.bat`** — it activates the venv, starts both the trading bot and dashboard, and opens your browser automatically.

### Manual Start

```bash
# 1. Start Ollama
ollama serve

# 2. Activate venv and run
cd trading-bot
venv\Scripts\activate
python runners\run_live_combined.py
```

**Daily schedule:**

| Time | Action |
|---|---|
| Script launch | Ollama warmup, earnings cache prefetch, Sentiment Alpha async trigger |
| `before_market_opens` (~9:00 AM) | Earnings cache cleared, regime pre-warmed, pre-market enrichment (gaps + news + sentiment) |
| 9:30 AM | Position sync from Alpaca |
| 9:30 AM onward | Overnight positions checked against SELL signals |
| 9:45 AM – noon | ORB / mean-reversion entries |
| Every 30 min | Regime classification refreshed |
| Every 5 min | Open positions monitored for stop/target hits |
| 3:45 PM | All leveraged ETF positions closed |
| 3:50 PM | Preliminary EOD signals — SELL signals acted on immediately |
| 4:15 PM | Final EOD signals (official close) — overwrites cache, SELL signals acted on |
| Overnight | Non-leveraged positions held until SELL signal |

### Switch to Live Trading

Set `ALPACA_IS_PAPER=false` in `.env`. Paper trade for at least 30–60 days first.

---

## Alert Scripts

```bash
# Morning ORB check — run at 9:45 AM ET
python alerts\run_orb_check.py

# EOD technical signals — run at 3:50 PM ET
python alerts\run_technical_signals.py
```

**Windows Task Scheduler:**

| Task | Script | Trigger |
|---|---|---|
| Morning ORB | `alerts\run_orb_check.py` | Daily 9:45 AM, Mon–Fri |
| EOD Signals | `alerts\run_technical_signals.py` | Daily 3:50 PM, Mon–Fri |

---

## Backtesting

### EMA Crossover (Yahoo Finance, free)
```bash
python runners\run_backtest_ema.py
```
3-year backtest (2022–2025). Outputs equity curve, Sharpe, max drawdown vs QQQ benchmark.

### ORB Base Strategy (Polygon.io)
```bash
python runners\run_backtest_orb.py
```
Baseline without AI/regime filter — useful for comparison.

### TrendFilteredORB Full Pipeline (Polygon.io)
```bash
python runners\run_backtest_combined.py
```
First run: ~5–10 min (Polygon fetch). Subsequent runs: instant from cache. AI/regime detection skipped in backtest for speed; uses simplified signal logic instead.

Configure date range in `runners/run_backtest_combined.py`:
```python
START = datetime(2024, 7, 1)
END   = datetime(2025, 7, 1)
```

---

## Notifications

| Channel | Setup |
|---|---|
| **Email** | Gmail App Password (requires 2FA), or any SMTP server |
| **Discord** | Webhook URL from Server Settings → Integrations |
| **Telegram** | Bot token from @BotFather, chat ID from @userinfobot |

Fires on: trade entry, trade exit (with P&L), EOD signal summary, critical errors.

---

## Symbols

Default `symbols.txt` covers 40 symbols across multiple sectors:

- **Broad market:** SPY, QQQ, SPMO, QQQM
- **Semiconductors:** SMH, NVDA, MU, TSM, AMAT, LRCX, SNDK, DRAM
- **Precious metals:** GLDM, PSLV, GDXJ, GDMN, GDE, ARIS, AG, PAAS, SLVP
- **Leveraged reference:** TQQQ, SQQQ
- **Tech/AI:** PLTR, ROBO
- **Crypto:** IBIT
- **Energy/commodities:** DBC, NANR, REMX
- **Space/defense:** UFO, RKLB
- **Uranium:** URA, URNM
- **International:** EWT, EWY, EWJV
- **Alternatives:** DBMF, GRID, CEG, JPM

---

## Risk Management

**Position level:**
- Maximum 8 simultaneous open positions (configurable)
- Base risk 1% of portfolio per trade
- AI confidence scales size 0.5×–2.0× (hard cap 2% effective risk)
- Stop loss at OR midpoint for ORB entries
- Leveraged ETFs always closed by 3:45 PM ET
- Direct-trade symbols held overnight until SELL signal or stop hit
- HOLD-bias trades: half size by default

**Safety guards:**
- Never SHORT a symbol with no inverse ETF
- Never hold opposing bull+bear ETFs simultaneously
- ORB entries only once per symbol per day
- No new entries after noon
- Low liquidity regime → skip entirely
- Earnings within 48 hours → skip
- AI confidence below 0.55 → skip
- Mean-reversion entries capped at 0.75× base risk
- Market hours guard: bot sleeps outside Mon–Fri 9:30 AM – 4:25 PM ET

---

## Future Enhancements

```
┌────────────────────┐     ┌──────────────────────────────┐
│   Trading Bot       │     │   Sentiment Trading Alpha     │
│   ORB/EMA/RSI       │     │   Geopolitical RSS + LLM      │
│   (this repo)       │     │   Jeff's Bot                  │
└────────┬───────────┘     └──────────────┬───────────────┘
         │ technical signal               │ sentiment signal
         └──────────────┬─────────────────┘
                        ▼
            ┌───────────────────────┐
            │    AI Fusion Layer     │
            │    Signal Agreement    │
            │    Confidence Ranking  │
            └──────────┬────────────┘
                       ▼
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  Regime Engine   Capitol Trades  Volatility
  (Market State)  (Political       (VIX/ATR)
                   Signals)
        └──────────────┼──────────────┘
                       ▼
               Final Probability Score → Dynamic Position Sizing → Alpaca
```

**Near-term:**
- [ ] Capitol Trades ingestion — congressional disclosure signals
- [ ] Per-symbol regime detection (currently QQQ proxy for all)
- [ ] Backtest TrendFilteredORB with earnings filter + regime switching enabled
- [ ] Cloud deployment — Oracle Cloud free tier ARM

**Medium-term:**
- [ ] Full Sentiment Trading Alpha integration
- [ ] Wheel strategy module (cash-secured puts)
- [ ] tastytrade broker support for options
- [ ] Options flow data as confirmation signal

**Long-term:**
- [ ] ML model trained on `trade_journal.db` to replace/augment LLM grader
- [ ] Multi-broker support (tastytrade, IBKR)
- [ ] Portfolio-level risk management (sector concentration, correlation-adjusted sizing)

---

## Dependencies

| Package | Purpose |
|---|---|
| `lumibot` | Backtesting framework and live broker abstraction |
| `alpaca-py` | Alpaca broker API |
| `alpaca-trade-api` | Legacy Alpaca API (required by LumiBot) |
| `pandas` / `numpy` | Data manipulation |
| `pandas-ta` | Technical indicators |
| `yfinance` | Yahoo Finance data + earnings calendar |
| `polygon-api-client` | Polygon.io intraday data for backtesting |
| `python-dotenv` | Environment variable management |
| `requests` | HTTP client for Ollama API calls |
| `pytz` | Timezone handling |
| `fastapi` / `uvicorn` | Dashboard API server |

Python 3.12 required. Python 3.13 and 3.14 not supported (numba/LumiBot constraint).

---

## Disclaimer

This software is for **educational and research purposes only**. It is not financial advice. Automated trading involves substantial risk of loss. Past performance does not guarantee future results. Leveraged and inverse ETFs can lose value rapidly and are not suitable for all investors.

Always paper trade first. Never risk capital you cannot afford to lose.