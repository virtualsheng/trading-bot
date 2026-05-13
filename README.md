# Trading Bot

An AI-enhanced algorithmic trading system for US equities and leveraged ETFs. Combines momentum-based technical analysis with Opening Range Breakout (ORB) execution, filtered and sized by a local LLM running via Ollama.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Signal Pipeline](#signal-pipeline)
- [AI Layer](#ai-layer)
- [Regime-Based Strategy Switching](#regime-based-strategy-switching)
- [Earnings Calendar Filter](#earnings-calendar-filter)
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

This bot implements a two-stage trading approach:

**Stage 1 — Afternoon technical signal (3:50 PM ET)**
Scans all symbols in `symbols.txt` using EMA crossovers, RSI, MACD, and SMA200 to generate a directional bias (BUY / SELL / HOLD) for the next session. Results are persisted to `cache/daily_bias.json`.

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
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│           SIGNAL LAYER (3:50 PM ET daily)               │
│  symbols.txt → signal_engine.py                         │
│  EMA 2/3/5 crossover + RSI + MACD + SMA50/200           │
│  → BUY / SELL / HOLD per symbol                         │
│  → cache/daily_bias.json (persisted overnight)          │
└──────────────────────────┬──────────────────────────────┘
                           │ prior-day bias
┌──────────────────────────▼──────────────────────────────┐
│           EXECUTION LAYER (9:45 AM – noon)              │
│  Morning ORB breakout detected                          │
│  Bias check: does breakout align with prior-day signal? │
│  Earnings filter: report within 48h? → skip             │
│  leverage_map.py → route to highest-leverage ETF pair   │
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
│  Setup grader → confidence 0.0–1.0 → size multiplier    │
└──────────────────────────┬──────────────────────────────┘
                           │ sized order
┌──────────────────────────▼──────────────────────────────┐
│            EXECUTION & LOGGING                          │
│  Alpaca API (paper or live)                             │
│  trade_journal.py → SQLite (cache/trade_journal.db)     │
│  Notifications → Email / Discord / Telegram             │
│  Leveraged ETFs closed at 3:45 PM                       │
│  Direct-trade symbols held overnight until SELL signal  │
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

The AI call only fires for symbols where a breakout is actually detected — not all 40 symbols on every iteration.

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

### Mean-Reversion Logic (`strategies/mean_reversion_strategy.py`)

When the regime is `ranging` or `mean_reversion` with confidence ≥ 0.70:

- Instead of entering with the breakout, the bot fades it
- Entry condition: price has broken the OR boundary by ≥ 0.3% AND RSI confirms overextension (>68 for short fades, <32 for long fades)
- Stop: OR boundary + 1× ATR beyond entry
- Target: OR midpoint (mean reversion target)
- Minimum 1:1 reward:risk required
- Sized at 0.75× normal risk (more uncertain than momentum)

---

## Earnings Calendar Filter

`strategies/earnings_filter.py` uses Yahoo Finance (free, no API key) to check the next earnings date for each symbol before entry.

- Blocks new positions within **48 hours** of a scheduled earnings report
- Results cached per symbol per day — only one Yahoo Finance call per symbol per session
- Cache cleared each morning in `before_market_opens()`
- **Fails open**: if the calendar cannot be fetched, the trade proceeds rather than blocking silently

Why this matters: leveraged ETFs (3× especially) can gap 20–30%+ overnight on earnings. A stop loss set intraday provides no protection against an overnight gap.

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
| Context | VIX level hook, SPY trend, sentiment score (ready for Jeff's bot) |
| Narrative | AI-generated plain-English journal entry |

**View performance stats:**
```python
from strategies.trade_journal import TradeJournal
stats = TradeJournal().get_stats(days=30)
# Returns: win rate, P&L, profit factor, Sharpe, by regime/symbol/AI tier
```

**Export to CSV:**
```python
TradeJournal().export_csv("my_trades.csv")
```

Browse the database visually with [DB Browser for SQLite](https://sqlitebrowser.org/) (free).

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
│   ├── run_backtest_combined.py     # ORB backtest with Polygon intraday data
│   ├── run_backtest_ema.py          # EMA crossover backtest (Yahoo daily data)
│   ├── run_backtest_orb.py          # ORB backtest (Yahoo, ~30 day limit)
│   ├── run_live_combined.py         # ★ Main live runner — starts TrendFilteredORB
│   ├── run_live_ema.py              # EMA strategy live runner
│   └── run_live_orb.py              # ORB-only live runner
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
├── symbols.txt                      # Master symbol list (one ticker per line)
├── setup.py                         # Package installation (pip install -e .)
├── requirements.txt                 # Python dependencies
└── .env                             # API keys — never commit this file
```

---

## Leveraged ETF Map

`strategies/leverage_map.py` maps each signal symbol to its highest-available leveraged ETF pair. The bot reads signals from the underlying (e.g. SMH) but executes in the leveraged vehicle (e.g. SOXL/SOXS).

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

Symbols with no liquid leveraged ETF trade the underlying directly. SHORT entries are automatically skipped for direct-trade symbols since there is no inverse ETF.

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

# Install project as editable package (fixes all import paths)
pip install -e .

# Pull the Ollama model
ollama pull qwen3:8b
```

### Environment Variables

Create `.env` in the project root. **Never commit this file.**

```env
# Alpaca — set ALPACA_IS_PAPER=false for live trading
ALPACA_API_KEY=your_api_key_here
ALPACA_API_SECRET=your_secret_key_here
ALPACA_IS_PAPER=true

# Polygon.io — only needed for intraday backtesting
POLYGON_API_KEY=your_polygon_key_here

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

One ticker per line. Lines starting with `#` are ignored.

```
# Broad market
SPY
QQQ

# Semiconductors
SMH
NVDA
```

### Strategy Parameters (`runners/run_live_combined.py`)

```python
PARAMS = {
    "orb_minutes":              15,     # Opening range window in minutes
    "bar_minutes":              5,      # Bar size
    "risk_pct":                 0.01,   # Base risk per trade (1% of portfolio)
    "reward_ratio":             2.0,    # 2:1 reward:risk target
    "eod_exit_time":            "15:45",
    "max_positions":            5,      # Max simultaneous open positions
    "ai_min_confidence":        0.55,   # Skip trades below this AI score
    "hold_override":            False,  # Trade HOLD-bias symbols (not recommended)
    "hold_override_size":       0.5,    # Size multiplier if hold_override=True
    "earnings_filter_enabled":  True,   # Skip entries near earnings
    "earnings_buffer_hours":    48,     # Hours before earnings to block entry
    "regime_switching_enabled": True,   # Use mean-reversion in ranging markets
    "mean_reversion_min_conf":  0.70,   # Min regime confidence for MR entries
}
```

### Ollama Model (`strategies/ai_engine.py`)

```python
OLLAMA_MODEL = "qwen3:8b"   # Change to llama3.2:3b for faster but lighter output
TIMEOUT      = 15            # Seconds before falling back to defaults
```

---

## Running the Bot

### 1. Start Ollama

```bash
ollama serve
```

### 2. Run the Live Strategy

```bash
cd trading-bot
venv\Scripts\activate
python runners\run_live_combined.py
```

**What happens on startup and throughout the day:**

| Time | Action |
|---|---|
| Script launch | Ollama warmup — model loaded into memory immediately |
| `before_market_opens` (~9:00 AM) | Earnings cache cleared, regime pre-warmed, signals run if no cache |
| 9:30 AM | Position sync from Alpaca (overnight positions reconciled) |
| 9:30 AM onward | Overnight positions checked against SELL signals |
| 9:45 AM – noon | ORB / mean-reversion entries attempted per symbol |
| Every 30 min | Regime classification refreshed for QQQ |
| Every 5 min | Open positions monitored for stop/target hits |
| 3:45 PM | All leveraged ETF positions closed |
| 3:50 PM | EOD signals run, bias cached for tomorrow |
| Overnight | Non-leveraged positions held until next SELL signal |

### 3. Switch to Live Trading

Set `ALPACA_IS_PAPER=false` in `.env`. Validate paper trading performance for at least 30–60 days first.

---

## Alert Scripts

These run independently — useful for manual monitoring or Windows Task Scheduler.

### Morning ORB Check (`alerts/run_orb_check.py`)

Run at 9:45–9:56 AM ET after the opening range has formed. Reports BUY / SELL / WAIT for every symbol and sends via email, Discord, Telegram.

```bash
python alerts\run_orb_check.py
```

### EOD Technical Signals (`alerts/run_technical_signals.py`)

Run at 3:50 PM ET. Single-pass scan — results are used for both the printed output and writing `cache/daily_bias.json`. Running this manually also populates the bias cache for the next day even if the live bot wasn't running.

```bash
python alerts\run_technical_signals.py
```

**Windows Task Scheduler:**

| Task | Script | Trigger |
|---|---|---|
| Morning ORB | `alerts\run_orb_check.py` | Daily 9:45 AM, Mon–Fri |
| EOD Signals | `alerts\run_technical_signals.py` | Daily 3:50 PM, Mon–Fri |

---

## Backtesting

### EMA Crossover Strategy (Yahoo Finance, free)

```bash
python runners\run_backtest_ema.py
```

3-year backtest (2022–2025) including the 2022 bear market. Outputs equity curve, Sharpe ratio, max drawdown, and win rate vs QQQ buy-and-hold benchmark.

### ORB Strategy (Polygon.io intraday data)

```bash
python runners\run_backtest_combined.py
```

Fetches 5-minute bars from Polygon for QQQ, TQQQ, SQQQ. First run takes 30–60 minutes due to the free-tier rate limit (5 requests/minute). Data is cached — subsequent runs load instantly.

Configure the date range in `runners/run_backtest_combined.py`:
```python
START = datetime(2024, 7, 1)
END   = datetime(2025, 7, 1)
```

---

## Notifications

All three channels are optional. Configure credentials in `.env`.

| Channel | Setup |
|---|---|
| **Email** | Gmail App Password (requires 2FA), or any SMTP server |
| **Discord** | Webhook URL from Server Settings → Integrations |
| **Telegram** | Bot token from @BotFather, chat ID from @userinfobot |

Notifications fire on: trade entry, trade exit (with P&L), EOD signal summary, and critical errors.

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

Add or remove symbols freely. New symbols not in `leverage_map.py` trade the underlying directly at 1×.

---

## Risk Management

**Position level:**
- Maximum 5 simultaneous open positions (configurable)
- Base risk 1% of portfolio per trade
- AI confidence scales size 0.5×–2.0× (hard cap 2% effective risk)
- Stop loss at OR midpoint for ORB entries
- Leveraged ETFs always closed by 3:45 PM ET
- Direct-trade symbols held overnight until SELL signal or stop hit

**Safety guards:**
- Never SHORT a symbol with no inverse ETF
- Never hold opposing bull+bear ETFs for the same underlying simultaneously
- ORB entries only taken once per symbol per day
- No new entries after noon
- Low liquidity regime → skip entirely
- Earnings within 48 hours → skip
- AI confidence below 0.55 → skip
- Mean-reversion entries capped at 0.75× base risk

---

## Future Enhancements

### Integration with Sentiment Trading Alpha

This bot is designed to integrate with **[Sentiment Trading Alpha](https://github.com/techjeffe/Sentiment-Trading-Alpha)** by Jeff Jeffe as a complementary signal layer. The `sentiment_score` and `sentiment_signal` fields are already present in the trade journal schema, ready for population once integration is built.

The planned combined architecture:

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
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  Regime Engine   Capitol Trades  Volatility
  (Market State)  (Political       (VIX/ATR)
                   Signals)
        └──────────────┼──────────────┘
                       ▼
               Final Probability Score
                       ▼
               Dynamic Position Sizing
                       ▼
                Alpaca Execution
```

### Planned Roadmap

**Near-term:**
- [ ] Capitol Trades ingestion — congressional trade disclosures as additional signal source
- [ ] Backtest TrendFilteredORB with earnings filter and regime switching enabled
- [ ] Per-symbol regime detection (currently uses QQQ as proxy for all symbols)
- [ ] Cloud deployment — Oracle Cloud free tier ARM instance

**Medium-term:**
- [ ] Jeff's Sentiment Trading Alpha integration — geopolitical RSS signal layer
- [ ] Wheel strategy module — cash-secured puts on high-conviction names
- [ ] tastytrade broker support for options execution
- [ ] Options flow data as confirmation signal

**Long-term:**
- [ ] ML model trained on `trade_journal.db` to replace/augment the LLM grader
- [ ] Multi-broker support (tastytrade options, IBKR international)
- [ ] Portfolio-level risk management (sector concentration, correlation-adjusted sizing)

---

## Disclaimer

This software is for **educational and research purposes only**. It is not financial advice. Automated trading involves substantial risk of loss. Past performance does not guarantee future results. Leveraged and inverse ETFs can lose value rapidly and are not suitable for all investors.

Always paper trade first. Never risk capital you cannot afford to lose. The authors take no responsibility for trading losses incurred using this software.

---

## Dependencies

| Package | Purpose |
|---|---|
| `lumibot` | Backtesting framework and live broker abstraction |
| `alpaca-py` | Alpaca broker API |
| `alpaca-trade-api` | Legacy Alpaca API (required by LumiBot) |
| `pandas` / `numpy` | Data manipulation and computation |
| `pandas-ta` | Technical indicators |
| `yfinance` | Yahoo Finance data (backtesting + earnings calendar) |
| `polygon-api-client` | Polygon.io intraday data for ORB backtesting |
| `python-dotenv` | Environment variable management |
| `requests` | HTTP client for Ollama API calls |
| `pytz` | Timezone handling |

Python 3.12 required. Python 3.13 and 3.14 not supported due to `numba` constraints in LumiBot.