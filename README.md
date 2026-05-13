# Technical Trading Bot

An AI-enhanced algorithmic trading system for US equities and leveraged ETFs, built on Python, LumiBot, and Alpaca. The bot combines momentum-based technical analysis with Opening Range Breakout (ORB) execution, filtered and sized by a local LLM (Ollama) running on your own machine — no cloud AI costs.

> **Status:** Active development — paper trading recommended until backtesting validation is complete.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Signal Pipeline](#signal-pipeline)
- [AI Layer](#ai-layer)
- [Trade Journal](#trade-journal)
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

This bot implements a two-stage trading approach inspired by professional intraday strategies:

1. **Afternoon technical signal** (3:50 PM ET) — scans all symbols in `symbols.txt` using EMA crossovers, RSI, MACD, and SMA200 to generate a directional bias (BUY / SELL / HOLD) for the next trading session.

2. **Morning ORB execution** (9:45 AM ET onward) — waits for price to break above or below the first 15 minutes of trading. Only executes breakouts that **align with the prior-day bias**, then routes the trade to the highest-leverage ETF available for that signal symbol.

Before any order is placed, a local LLM running via Ollama grades the setup quality (0.0–1.0 confidence) and classifies the current market regime. Position size scales dynamically with confidence — elite setups get 2x normal size, weak setups get 0.5x, and setups below the threshold are skipped entirely.

Every trade is recorded to a SQLite journal with full context: signal metadata, AI grading, regime classification, P&L, R-multiple, and an AI-generated narrative entry. This becomes training data for future model fine-tuning.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     SIGNAL LAYER (3:50 PM ET)                   │
│                                                                  │
│   symbols.txt → signal_engine.py → EMA/RSI/MACD/SMA analysis   │
│                         ↓                                        │
│              cache/daily_bias.json (persisted overnight)         │
└──────────────────────────────┬──────────────────────────────────┘
                               │ prior-day bias
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│                    EXECUTION LAYER (9:45 AM ET)                  │
│                                                                  │
│   Morning ORB breakout detected                                  │
│          ↓                                                        │
│   Bias check: does breakout align with prior-day signal?         │
│          ↓                                                        │
│   leverage_map.py → route to highest-leverage ETF pair           │
└──────────────────────────────┬──────────────────────────────────┘
                               │ candidate trade
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│                      AI FUSION LAYER (Ollama)                    │
│                                                                  │
│   ai_engine.py                                                   │
│   ├── setup_grader:    OHLC candles → confidence 0.0–1.0         │
│   ├── regime_detector: 5m/15m/1h bars → market regime            │
│   └── trade_narrator:  post-trade → journal narrative            │
│                                                                  │
│   Confidence → position size multiplier                          │
│   Regime     → stop/target distance adjustment                   │
└──────────────────────────────┬──────────────────────────────────┘
                               │ sized order
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│                        EXECUTION & LOGGING                       │
│                                                                  │
│   Alpaca API (paper or live)                                     │
│   trade_journal.py → SQLite (cache/trade_journal.db)             │
│   Notifications → Email / Discord / Telegram                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Signal Pipeline

### Technical Signal Engine (`strategies/signal_engine.py`)

Runs on 400 days of daily bars fetched from Alpaca. Computes:

| Indicator | Use |
|---|---|
| EMA 2/3/5 crossover | Short-term momentum direction |
| SMA 50 / SMA 200 | Trend regime filter |
| RSI(14) | Overbought/oversold, controls entry threshold |
| MACD histogram | Momentum confirmation |
| Volume ratio (20-day avg) | Conviction filter |

**Signal outputs:**

| Action | Criteria |
|---|---|
| `STRONG_BUY` | Bull score ≥ 5, RSI < 68, volume > 1.1x avg |
| `BUY` | Bull score ≥ 4, RSI < 62 |
| `STRONG_SELL` | Bear score ≥ 5, RSI > 32, volume > 1.1x avg |
| `SELL` | Bear score ≥ 4, RSI > 38 |
| `HOLD` | No clear signal |

### Opening Range Breakout (`core/orb.py`, `strategies/orb_strategy.py`)

- **Opening range:** first 15 minutes of the session (9:30–9:45 AM ET) using 5-minute bars
- **Entry signal:** 5-minute close above OR high (bullish) or below OR low (bearish)
- **Stop loss:** midpoint of the opening range
- **Profit target:** 2× the risk distance from entry to stop (configurable)
- **EOD exit:** all positions closed by 3:45 PM ET regardless of outcome

---

## AI Layer

All AI runs locally via **Ollama** (`localhost:11434`). No API keys, no cost, no data leaving your machine. Default model: `qwen3:8b`. Change in `strategies/ai_engine.py`.

### Setup Grader

Before each trade executes, the last 25 five-minute candles are passed to the LLM with a structured prompt asking it to evaluate:

- Volume quality relative to morning average (increasing = bullish for breakouts)
- Price coiling tightness before breakout (tight consolidation = higher quality)
- Breakout extension (overextended / parabolic = lower quality)
- Candle body strength at the moment of breakout
- Successive momentum bars in the breakout direction

Returns a **confidence score (0.0–1.0)** and position size multiplier:

| Confidence | Size Multiplier | Effective Risk |
|---|---|---|
| ≥ 0.90 (Elite) | 2.0× | 2.0% of portfolio |
| ≥ 0.75 (Strong) | 1.5× | 1.5% |
| ≥ 0.65 (Normal) | 1.0× | 1.0% |
| ≥ 0.55 (Weak) | 0.5× | 0.5% |
| < 0.55 | Skip | Trade not taken |

### Regime Detector

Every 30 minutes, multi-timeframe bar data (5m, 15m, 1h) is analyzed to classify the current market regime:

| Regime | ORB Suitability | Bot Behavior |
|---|---|---|
| `trending_up` | Good | Normal execution |
| `trending_down` | Good | Normal execution |
| `ranging` | Moderate | Tighter targets |
| `volatile` | Moderate | Wider stops |
| `mean_reversion` | Poor | Skip unless AI confidence ≥ 0.80 |
| `low_liquidity` | Poor | Skip unless AI confidence ≥ 0.80 |

Stop and target distances are adjusted by the regime multipliers returned by the LLM.

### HOLD Override

If the prior-day bias is HOLD but a strong ORB breakout fires and no position is currently open, the trade is taken at **0.5× normal size** rather than skipped. This captures surprise momentum moves while limiting exposure when there is no directional conviction.

---

## Trade Journal

Every trade is recorded to **`cache/trade_journal.db`** (SQLite). Fields tracked:

| Category | Fields |
|---|---|
| Identity | date, symbol, exec ticker, direction |
| Signal | action, source, bull/bear score, RSI, volume ratio |
| Entry | time, price, quantity, OR levels, breakout extension % |
| AI Grading | confidence, size multiplier, flags, volume quality, PA quality |
| Regime | regime type, confidence, ORB suitability, stop/target adjustments |
| Risk | initial stop, initial target, risk %, planned R |
| Exit | time, price, reason (STOP/TARGET/EOD/MANUAL) |
| Outcome | P&L, P&L %, R-multiple achieved, WIN/LOSS/BREAKEVEN |
| Context | VIX level, SPY trend, sentiment score (hook for Jeff's bot) |
| Narrative | AI-generated plain-English journal entry |

**Export to CSV** at any time:

```python
from strategies.trade_journal import TradeJournal
TradeJournal().export_csv("my_trades.csv")
```

**View performance stats:**

```python
stats = TradeJournal().get_stats(days=30)
# Returns win rate, P&L, profit factor, Sharpe, breakdown by regime/symbol/AI tier
```

Browse the database visually with [DB Browser for SQLite](https://sqlitebrowser.org/) (free).

---

## Project Structure

```
trading-bot/
│
├── strategies/                    # LumiBot strategy classes and signal logic
│   ├── ai_engine.py               # Ollama setup grader, regime detector, narrator
│   ├── ema_crossover_strategy.py  # Daily EMA crossover strategy (LumiBot)
│   ├── leverage_map.py            # Symbol → leveraged ETF pair registry
│   ├── orb_strategy.py            # Base ORB strategy (LumiBot)
│   ├── signal_combiner.py         # Combines technical + sentiment signals
│   ├── signal_engine.py           # EMA/RSI/MACD technical signal generator
│   ├── trade_journal.py           # SQLite trade journaling and stats
│   └── trend_filtered_orb.py      # Main live strategy (bias + ORB + AI)
│
├── core/                          # Shared data and indicator utilities
│   ├── data.py                    # Alpaca data fetcher (daily + intraday)
│   ├── indicators.py              # EMA, RSI, MACD implementations
│   └── orb.py                     # ORB signal for standalone alert scripts
│
├── runners/                       # Entry points for backtesting and live trading
│   ├── run_backtest_combined.py   # ORB backtest with Polygon intraday data
│   ├── run_backtest_ema.py        # EMA crossover backtest (Yahoo daily data)
│   ├── run_backtest_orb.py        # ORB backtest (Yahoo, limited to 30 days)
│   ├── run_live_combined.py       # ★ Main live runner — starts TrendFilteredORB
│   ├── run_live_ema.py            # EMA strategy live runner
│   └── run_live_orb.py            # ORB-only live runner
│
├── alerts/                        # Standalone daily alert scripts (cron/scheduler)
│   ├── run_orb_check.py           # 9:45 AM — morning ORB breakout report
│   └── run_technical_signals.py   # 3:50 PM — EOD signal scan + bias cache write
│
├── notifications/                 # Notification channel adapters
│   ├── discord.py                 # Discord webhook
│   ├── emailer.py                 # SMTP email
│   └── telegram.py                # Telegram bot
│
├── cache/                         # Runtime data (gitignored)
│   ├── daily_bias.json            # Prior-day signal cache (written EOD, read AM)
│   ├── trade_journal.db           # SQLite trade database
│   └── *.csv                      # Polygon intraday data cache
│
├── logs/                          # Signal and execution logs (gitignored)
│   └── daily_signals.log
│
├── symbols.txt                    # Master symbol list (one ticker per line)
├── setup.py                       # Package installation (pip install -e .)
├── requirements.txt               # Python dependencies
└── .env                           # API keys — never commit this file
```

---

## Leveraged ETF Map

`strategies/leverage_map.py` maps each signal symbol to its highest-available leveraged ETF pair. The bot reads from the signal symbol (e.g. SMH) but executes in the leveraged vehicle (e.g. SOXL/SOXS). This gives leverage without requiring a margin account.

Key mappings (highest leverage available for each sector):

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
| UFO / RKLB / URA / URNM / EWT / EWJV / DBMF / GRID / CEG / REMX / DBC | (direct) | (direct) | 1× | No quality leveraged pair |

Symbols with no liquid leveraged ETF pair trade the underlying directly at 1×.

---

## Setup

### Prerequisites

- Python 3.12 (not 3.13 or 3.14 — dependency compatibility)
- [Ollama](https://ollama.com) installed and running locally
- [Alpaca](https://alpaca.markets) paper trading account
- [Polygon.io](https://polygon.io) free API key (for intraday backtesting only)

### Installation

```bash
# Clone the repo
git clone https://github.com/virtualsheng/trading-bot.git
cd trading-bot

# Create virtual environment with Python 3.12
py -3.12 -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

# Install dependencies
pip install --upgrade pip
pip install lumibot pandas numpy python-dotenv yfinance pandas-ta
pip install alpaca-trade-api alpaca-py requests polygon-api-client pytz

# Install project as editable package (fixes all import paths)
pip install -e .

# Pull the Ollama model
ollama pull qwen3:8b
```

### Environment Variables

Create a `.env` file in the project root. **Never commit this file.**

```env
# Alpaca (paper trading — switch ALPACA_IS_PAPER=false for live)
ALPACA_API_KEY=your_alpaca_api_key
ALPACA_API_SECRET=your_alpaca_secret_key
ALPACA_IS_PAPER=true

# Polygon.io (free tier — only needed for intraday backtesting)
POLYGON_API_KEY=your_polygon_api_key

# Email notifications (optional)
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

One ticker per line. Lines starting with `#` are comments. The bot scans all symbols for EOD signals and processes ORB setups for any that have a BUY or SELL bias.

```
# Broad market
SPY
QQQ

# Semiconductors
SMH
NVDA

# Precious metals
GLDM
GDXJ
```

### Strategy Parameters (`runners/run_live_combined.py`)

```python
PARAMS = {
    "orb_minutes":       15,     # Opening range window in minutes
    "bar_minutes":       5,      # Bar size
    "risk_pct":          0.01,   # Base risk per trade (1% of portfolio)
    "reward_ratio":      2.0,    # 2:1 reward:risk target
    "eod_exit_time":     "15:45",
    "max_positions":     3,      # Maximum simultaneous open positions
    "ai_min_confidence": 0.55,   # Minimum AI confidence to take a trade
    "hold_override_size": 0.5,   # Size multiplier for HOLD-bias overrides
}
```

### Ollama Model (`strategies/ai_engine.py`)

```python
OLLAMA_MODEL = "qwen3:8b"   # Change to llama3.2:3b for faster/lighter
TIMEOUT      = 30            # Seconds before giving up on Ollama
```

---

## Running the Bot

### Start Ollama (required for AI features)

```bash
ollama serve
```

### Paper Trading (recommended to start)

```bash
cd trading-bot
venv\Scripts\activate
python runners\run_live_combined.py
```

The bot will:
1. Load yesterday's bias from `cache/daily_bias.json` (or run signals immediately if cache is empty)
2. Begin monitoring for ORB setups at 9:30 AM ET
3. Grade each setup with Ollama before executing
4. Close all positions by 3:45 PM ET
5. Run EOD signals at 3:50 PM ET and cache results for tomorrow

### Switch to Live Trading

Set `ALPACA_IS_PAPER=false` in your `.env` file. Ensure you have funded your live Alpaca account. Start with small position sizes and verify paper trading performance first.

---

## Alert Scripts

These scripts run independently of the live bot and are designed for manual monitoring and scheduled execution.

### Morning ORB Check (`alerts/run_orb_check.py`)

Run at 9:45–9:56 AM ET after the opening range has formed. Reports breakout status for every symbol.

```bash
python alerts\run_orb_check.py
```

**Windows Task Scheduler:** Daily trigger at 9:45 AM, Monday–Friday.

Output example:
```
🚀 QQQ:  BUY  | Current=487.32 | ORH=485.10 | Chg=+0.82% | Vol=1.67x
🔻 SQQQ: SELL | Current=8.41   | ORH=8.89   | Chg=-1.02% | Vol=1.43x
   SPY:  WAIT | Current=541.20 | ORH=542.80 | Chg=+0.12% | Inside Range
```

### EOD Technical Signals (`alerts/run_technical_signals.py`)

Run at 3:50 PM ET. Scans all symbols, prints directional signals, writes `cache/daily_bias.json` for tomorrow's live session.

```bash
python alerts\run_technical_signals.py
```

**Important:** Running this script manually at EOD also populates the bias cache, so the live bot will have fresh signals even if it wasn't running during market hours.

Output example:
```
🔥 GDXJ:  STRONG_BUY  | RSI=58.3 (Neutral)    | Chg=+2.24% | Vol=1.16x | Bull=5 Bear=0
🚀 UFO:   BUY         | RSI=57.9 (Neutral)    | Chg=+2.82% | Vol=3.11x | Bull=4 Bear=0
📉 PLTR:  SELL        | RSI=40.7 (Neutral)    | Chg=+1.31% | Vol=1.06x | Bull=1 Bear=4
   SPY:   HOLD        | RSI=80.7 (Overbought) | Chg=+0.32% | Vol=0.93x | Bull=4 Bear=1
```

---

## Backtesting

### EMA Crossover Strategy (daily bars, Yahoo Finance — free)

```bash
python runners\run_backtest_ema.py
```

Backtests the EMA crossover strategy from 2022–2025, including the 2022 bear market. Outputs a tearsheet with equity curve, Sharpe ratio, max drawdown, and win rate vs QQQ buy-and-hold benchmark.

### ORB Strategy (5-minute intraday bars, Polygon.io)

```bash
python runners\run_backtest_combined.py
```

Fetches 5-minute bars from Polygon for QQQ, TQQQ, and SQQQ. First run takes 30–60 minutes due to Polygon free-tier rate limiting (5 requests/minute). Data is cached locally — subsequent runs load instantly.

**Backtest period** is configured in the runner file:

```python
START = datetime(2024, 7, 1)
END   = datetime(2025, 7, 1)
```

Results are saved to the `logs/` folder. The performance summary prints directly to terminal including: total return, win rate, profit factor, Sharpe ratio, max drawdown, exit breakdown (stops/targets/EOD), and best/worst trades.

---

## Notifications

Three notification channels are supported, all optional. Configure credentials in `.env`.

| Channel | Setup |
|---|---|
| **Email** | Gmail with App Password (2FA required), or any SMTP server |
| **Discord** | Create a Discord webhook in your server settings |
| **Telegram** | Create a bot via @BotFather, get your chat ID via @userinfobot |

Notifications are sent on:
- Trade entry (symbol, direction, price, quantity, AI confidence)
- Trade exit (P&L, exit reason)
- EOD signal summary (buy/sell count, high-conviction names)
- Any critical errors

---

## Symbols

The default `symbols.txt` contains 39 symbols across multiple sectors:

- **Broad market:** SPY, QQQ, SPMO, QQQM
- **Semiconductors:** SMH, NVDA, MU, TSM, AMAT, LRCX, SNDK, DRAM
- **Precious metals:** GLDM, PSLV, GDXJ, GDMN, GDE, ARIS, AG, PAAS, SLVP
- **Leveraged reference:** TQQQ, SQQQ
- **Tech/AI:** PLTR, ROBO
- **Crypto:** IBIT
- **Energy/commodities:** DBC, NANR, REMX
- **Space/defense:** UFO, RKLB
- **Uranium:** URA, URNM
- **International:** EWT, EWJV
- **Alternatives:** DBMF, GRID, CEG, JPM

Add or remove symbols by editing `symbols.txt`. The leverage map will automatically route new symbols to direct trading if no leveraged pair is configured.

---

## Risk Management

**Position level:**
- Maximum 3 simultaneous open positions
- Base risk of 1% of portfolio per trade
- AI confidence scales position size (0.5×–2.0×)
- Hard stop at the opening range midpoint
- All positions exit at 3:45 PM ET regardless of outcome

**Account level:**
- Never risk more than 2% on any single trade (cap on AI size scaling)
- HOLD-override trades are automatically half-sized
- Poor regime trades require AI confidence ≥ 0.80 to proceed

**Recommended minimums:**
- Paper trading: any amount
- Live trading: $5,000+ for meaningful position sizing at 1% risk with leveraged ETFs
- The wheel strategy (future): $10,000+ for cash-secured puts on individual stocks

---

## Future Enhancements

### Integration with Sentiment Trading Alpha

This bot is designed to eventually integrate with **[Sentiment Trading Alpha](https://github.com/techjeffe/Sentiment-Trading-Alpha)** by Jeff Jeffe as a complementary signal layer. The architecture is already prepared for this merger.

The planned combined architecture:

```
┌─────────────────────┐      ┌──────────────────────────┐
│   Technical Bot      │      │   Sentiment Trading Alpha │
│   ORB/EMA/RSI        │      │   Geopolitical RSS + LLM  │
│   (this repo)        │      │   Jeff's Bot              │
└──────────┬──────────┘      └────────────┬─────────────┘
           │                              │
           │  technical signal            │  sentiment signal
           └──────────────┬───────────────┘
                          ↓
              ┌───────────────────────┐
              │    AI Fusion Layer     │
              │    Confidence Ranking  │
              │    Signal Agreement    │
              └──────────┬────────────┘
                         │
         ┌───────────────┼───────────────┐
         ↓               ↓               ↓
    Regime Engine   Capitol Trades   Volatility
    (Market State)  (Political        (VIX/ATR)
                     Signals)
         │               │               │
         └───────────────┼───────────────┘
                         ↓
                  Final Probability Score
                         ↓
                  Dynamic Position Sizing
                         ↓
                   Alpaca Execution
```

**Technical signal** (this bot) + **Sentiment signal** (Jeff's bot) must agree before a trade is taken. Disagreement = skip or half-size. Agreement = full or scaled-up size based on AI fusion confidence.

The `sentiment_score` and `sentiment_signal` fields already exist in the trade journal schema, ready to be populated once integration is built.

### Planned Roadmap

**Near-term:**
- [ ] Earnings calendar filter — avoid new positions within 48 hours of a report
- [ ] Capitol Trades ingestion — congressional trade disclosures as additional signal source
- [ ] Backtest the full TrendFilteredORB strategy (currently only base ORB is backtested)
- [ ] Regime-based strategy switching (ORB in trending, mean-reversion in ranging)

**Medium-term:**
- [ ] Jeff's Sentiment Trading Alpha integration — RSS geopolitical signal layer
- [ ] Signal agreement scoring — weighted combination of technical + sentiment
- [ ] Wheel strategy module — cash-secured puts on high-conviction names during low-volatility regimes
- [ ] Options flow data integration — unusual options activity as confirmation signal

**Long-term:**
- [ ] ML model trained on `trade_journal.db` to replace or augment the LLM grader
- [ ] Multi-broker support (tastytrade for options, IBKR for international)
- [ ] Portfolio-level risk management (sector concentration limits, correlation-adjusted sizing)
- [ ] Web dashboard for real-time monitoring without running Jeff's separate frontend

---

## Disclaimer

This software is for **educational and research purposes only**. It is not financial advice. Automated trading involves substantial risk of loss. Past backtest performance does not guarantee future results. Leveraged ETFs can lose value rapidly and are not suitable for all investors.

- Always paper trade first and validate performance over multiple months
- Never risk capital you cannot afford to lose
- Understand the instruments you are trading before going live
- The authors take no responsibility for trading losses incurred using this software

---

## Dependencies

| Package | Purpose |
|---|---|
| `lumibot` | Backtesting framework and live broker abstraction |
| `alpaca-py` | Alpaca broker API client |
| `alpaca-trade-api` | Legacy Alpaca API (required by LumiBot) |
| `pandas` / `numpy` | Data manipulation and numerical computation |
| `pandas-ta` | Technical indicators (RSI, MACD, EMA) |
| `yfinance` | Yahoo Finance data for daily backtesting |
| `polygon-api-client` | Polygon.io intraday data for ORB backtesting |
| `python-dotenv` | Environment variable management |
| `requests` | HTTP client for Ollama and Polygon direct API calls |
| `pytz` | Timezone handling for market hours |

Python 3.12 is required. Python 3.13 and 3.14 are not supported due to `numba` dependency constraints in LumiBot.