# trading-bot

AI-enhanced algorithmic ORB trading bot for US equities and leveraged ETFs. Trades **QQQ, SMH, and USO** — entering leveraged ETF positions on Opening Range Breakout signals filtered by prior-day technical bias. Runs on an Alpaca cash account, up to 3 concurrent positions, all closed by end of day.

> **Swing trading signals for longer-term accounts** are handled by the separate [`swing_signal_engine`](https://github.com/virtualsheng/swing_signal_engine) project.

---

## How it works

```
EOD Technical Signal (EMA/RSI/MACD/SMA) — runs after close
  ↓  stored in cache/daily_bias.json
Pre-market (9:30 AM) — position sync from Alpaca
  ↓
Opening Range forms 9:30–9:45 AM (first 15 min of trading)
  ↓
ORB window 9:45–10:30 AM — enter if price breaks OR high/low
  AND aligns with prior-day bias
  ↓
Ollama AI Setup Grader → confidence score → position size multiplier
  ↓
Regime Detector (trending / ranging / volatile / mean_reversion)
  ↓
Options Expected Move → validates stop, sets EM exit boundary
  ↓
Entry: BUY leveraged ETF (TQQQ / SOXL / UCO) or inverse (SQQQ / SOXS / SCO)
  ↓
Trailing stop (2%) ratchets up as price rises
  ↓
Exit: trail fires  OR  EM boundary hit  OR  EOD close at 3:50 PM
```

---

## Signal symbols and execution ETFs

| Signal | Bull ETF | Bear ETF | Leverage | Notes |
|--------|----------|----------|----------|-------|
| QQQ | TQQQ | SQQQ | 3× | Nasdaq-100 |
| SMH | SOXL | SOXS | 3× | Semiconductors |
| USO | UCO | SCO | 2× | Crude oil |

**We never short-sell. Every order submitted is a BUY.**

| Signal | Breakout direction | Execution |
|--------|--------------------|-----------|
| BUY / STRONG_BUY | Price > OR High + 0.1% | BUY bull ETF (TQQQ / SOXL / UCO) |
| SELL / STRONG_SELL | Price < OR Low − 0.1% | BUY inverse ETF (SQQQ / SOXS / SCO) |
| HOLD | Price > OR High + 0.1% | BUY bull ETF at 0.5× size |

---

## Account setup

| Setting | Value |
|---------|-------|
| Broker | Alpaca |
| Account type | **CASH** (not margin) |
| Starting capital | $2,000 |
| PDT rule | Does not apply to cash accounts |
| Settlement | T+1 — all leveraged ETFs close EOD so funds settle overnight |
| Max positions | 3 (one per signal symbol) |

---

## Strategy parameters

All parameters live in `PARAMS` inside `runners/run_live_combined.py`.

| Parameter | Value | Description |
|-----------|-------|-------------|
| `risk_pct` | `0.10` | 10% max loss per trade — ~$200 on $2k |
| `max_positions` | `3` | One concurrent position per symbol |
| `max_position_pct` | `1.0` | Full account deployable; conviction-weighted split when multiple signals fire simultaneously |
| `eod_exit_time` | `15:50` | Force-close all leveraged ETFs at 3:50 PM |
| `stop_mode` | `or_low` | Stop placed at Opening Range low |
| `stop_delay_minutes` | `15` | Stop inactive for first 15 min — avoids stop-hunt wicks |
| `trail_stop_pct` | `0.02` | 2% trailing stop — ratchets up, never down |
| `target_exit` | `False` | No hard target — trail + EOD handles all exits |
| `em_boundary_exit` | `True` | Close if price hits options expected move upper boundary |
| `ai_min_confidence` | `0.55` | Minimum Ollama confidence to enter |
| `min_stop_pct` | `0.005` | Stop floor, scaled ×3 for 3× ETFs, ×2 for 2× ETFs |
| `min_breakout_pct` | `0.001` | Price must clear OR high/low by 0.1% to trigger |
| `orb_minutes` | `15` | Opening range = first 15 minutes (9:30–9:45 AM) |
| `reward_ratio` | `2.0` | 2:1 reference target (logged only — not a hard exit) |

---

## Exit logic

Three ways a position closes — whichever fires first:

**1. Trailing stop** — arms 15 minutes after entry. Ratchets up 2% below the highest price seen. Catches genuine reversals while surviving TQQQ/SOXL intrabar noise (~0.9–1.5%).

**2. EM boundary exit** — if price touches the options expected move upper boundary, position closes. Prevents giving back gains on overextended moves.

**3. EOD close at 3:50 PM** — all leveraged ETFs force-closed while market is still open. A safety net in `after_market_closes()` catches anything that slipped through.

---

## Capital allocation

When multiple symbols trigger simultaneously inside the ORB window, capital is split **proportional to conviction score**:

```
total_pool = min(portfolio_value, available_cash)   ← uses cash, not tied-up capital
QQQ share  = total_pool × (QQQ_conviction / sum_all_convictions)
SMH share  = total_pool × (SMH_conviction / sum_all_convictions)
```

If QQQ triggers at 9:50 AM alone and deploys the full account, a USO signal at 10:05 AM will find `get_cash() ≈ $0` and skip cleanly — no broken order.

---

## Daily schedule

| Time (ET) | Event |
|-----------|-------|
| Script start | Ollama warmup + bias refresh from cache |
| ~9:00 AM | Earnings cache cleared, regime pre-warmed |
| 9:30 AM | Position sync from Alpaca |
| 9:45–10:30 AM | ORB entry window (2-min iterations) |
| 10:30 AM–3:50 PM | Monitor + trailing stop (5-min iterations) |
| 3:50 PM | All leveraged ETFs force-closed (market hours) |
| ~4:05 PM | FINAL EOD signals written to `cache/daily_bias.json` |

---

## AI grading (Ollama)

Each ORB setup is graded by a local Ollama model (`qwen3:8b`) before entry:

- Scores 0.0–1.0 based on last 25 candles of price action
- Confidence < `ai_min_confidence` (0.55) → trade skipped
- Confidence maps to a position size multiplier: 0.5×–2.0× of base risk
- Runs locally — zero API cost
- A warmup call fires at startup to prevent timeout cascades across all symbols

---

## Regime detection

Runs every 30 minutes, classifies market as one of:

| Regime | Entry behavior |
|--------|---------------|
| `trending` | Standard ORB momentum entry |
| `ranging` | Mean-reversion fade entry |
| `volatile` | Tighter sizing |
| `mean_reversion` | Fade entries |
| `low_liquidity` | Skips entry |

---

## Earnings filter

New entries are blocked within 48 hours of a scheduled earnings report (via Yahoo Finance). All ETF symbols (QQQ, SMH, USO, TQQQ, SQQQ, SOXL, SOXS, UCO, SCO) are whitelisted — ETFs do not have earnings.

---

## Project structure

```
trading-bot/
├── runners/
│   ├── run_live_combined.py      # ★ Live trading — QQQ + SMH + USO
│   └── run_backtest_combined.py  # Backtest runner
│
├── strategies/
│   ├── trend_filtered_orb.py     # ★ Main strategy (1,818 lines)
│   ├── leverage_map.py           # Signal → leveraged ETF mapping
│   ├── ai_engine.py              # Ollama grading + regime detection
│   ├── signal_engine.py          # Technical signal (EMA/RSI/MACD/SMA)
│   ├── expected_move.py          # Options ATM straddle EM
│   ├── earnings_filter.py        # Yahoo Finance earnings calendar
│   └── trade_journal.py          # SQLite trade log
│
├── notifications/
│   ├── emailer.py
│   ├── discord.py
│   └── telegram.py
│
├── cache/                        # Auto-generated, not committed
│   ├── daily_bias.json           # Today's BUY/SELL/HOLD per symbol
│   └── trade_journal.db          # SQLite trade history
│
├── .env                          # Credentials — never commit
├── .env.example
└── requirements.txt
```

---

## Setup

### Prerequisites

- Python 3.12 (3.14 causes numba compatibility errors — use 3.12)
- [Ollama](https://ollama.com) running locally with `qwen3:8b`
- Alpaca account (paper or live)
- Polygon.io free tier (for intraday 5-min bars in backtest)

### Install

```bash
git clone https://github.com/virtualsheng/trading-bot.git
cd trading-bot

py -3.12 -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

pip install -r requirements.txt
```

### Configure

```bash
copy .env.example .env
# Fill in ALPACA_API_KEY, ALPACA_API_SECRET, POLYGON_API_KEY
# Set ALPACA_IS_PAPER=true for paper trading
```

### Run

```bash
# Paper trading (default)
python runners/run_live_combined.py

# Backtest (Feb–May 2025 by default — edit START/END in the runner)
python runners/run_backtest_combined.py
```

---

## Environment variables

```env
# Alpaca (required)
ALPACA_API_KEY=your_key
ALPACA_API_SECRET=your_secret
ALPACA_IS_PAPER=true          # true = paper, false = live real money

# Polygon.io (required for backtest intraday data)
POLYGON_API_KEY=your_key

# Notifications (optional — any combination works)
EMAIL_SENDER=you@gmail.com
EMAIL_PASSWORD=your_gmail_app_password
EMAIL_RECIPIENT=you@example.com
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## Backtest results (Feb–May 2025)

| Period | Symbols | Start | End | Return |
|--------|---------|-------|-----|--------|
| Feb 1 – May 1 2025 | QQQ + SMH | $2,000 | ~$3,200 | +60% |
| Feb 1 – May 1 2025 | QQQ + SMH + USO | $2,000 | $3,636 | +82% |

Period includes the March–April 2025 tariff drawdown. The bot correctly switched to inverse ETFs (SQQQ/SOXS/SCO) during the sell-off, capturing gains on both sides.

---

## Risk disclosure

- `risk_pct=0.10` is the **stop distance per trade**, not a daily loss limit
- With `max_positions=3` and `max_position_pct=1.0`, worst-case single-day loss if all 3 positions hit stops simultaneously is ~20–30%
- Leveraged ETFs decay over time — this bot holds them **intraday only**, never overnight
- USO/UCO/SCO carry additional geopolitical risk (oil is more event-driven than equities)
- Past backtest performance does not guarantee future results

---

## Disclaimer

For educational and research purposes only. Not financial advice. Always verify before executing with real money.