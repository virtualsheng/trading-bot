# trading-bot

AI-enhanced algorithmic trading bot for US equities. Trades **QQQ only** — buys TQQQ (3× bull) on BUY signals and SQQQ (3× bear) on SELL signals. Runs on a $2,000 Alpaca cash account with one trade per day.

> **Swing trading signals for retirement accounts** are handled by the separate [`swing_signal_engine/`](../swing_signal_engine) project.

---

## How it works

```
EOD Technical Signal (EMA/RSI/MACD/SMA) on QQQ
  ↓  stored in cache/daily_bias.json
Morning ORB Breakout (9:45 AM — if aligns with bias)
  ↓
Ollama AI Setup Grader → confidence score + size multiplier
  ↓
Regime Detector (trending / ranging / volatile)
  ↓
Options Expected Move → validates stop distance, sets EM exit boundary
  ↓
Position entry: BUY TQQQ or BUY SQQQ
  ↓
Trailing stop (2%) ratchets up as price rises
  ↓
Exit: trail fires  OR  EM boundary hit  OR  EOD close at 3:45 PM
```

---

## Account setup

| Setting | Value |
|---|---|
| Broker | Alpaca |
| Account type | **CASH** (not margin) |
| Starting capital | $2,000 |
| PDT rule | Does not apply to cash accounts |
| Settlement | T+1 — fine for 1 trade/day |
| Signal symbol | QQQ |
| Bull execution | TQQQ (3× Nasdaq bull) |
| Bear execution | SQQQ (3× Nasdaq bear) |

---

## Trade model

**We never short-sell. Every order submitted is a BUY.**

| Signal | Breakout direction | Execution |
|---|---|---|
| BUY / STRONG_BUY | Price > OR High + 0.1% | BUY TQQQ |
| SELL / STRONG_SELL | Price < OR Low − 0.1% | BUY SQQQ |
| HOLD | Price > OR High + 0.1% | BUY TQQQ at 0.5× size |

---

## Strategy parameters

All parameters are set in `PARAMS` inside `runners/run_live_combined.py`:

| Parameter | Value | Description |
|---|---|---|
| `risk_pct` | `0.02` | 2% risk per trade = ~$40 on $2k |
| `max_positions` | `1` | One trade at a time |
| `max_position_pct` | `0.40` | 40% cap = ~$800 per position |
| `stop_mode` | `"or_low"` | Stop at Opening Range low |
| `stop_delay_minutes` | `15` | Stop inactive for first 15 min |
| `trail_stop_pct` | `0.02` | 2% trailing stop |
| `target_exit` | `False` | No hard target — trail + EOD exits |
| `em_boundary_exit` | `True` | Close if price hits options EM upper boundary |
| `reward_ratio` | `2.0` | 2:1 reference target |
| `ai_min_confidence` | `0.55` | Minimum AI confidence to enter |
| `min_stop_pct` | `0.005` | Floor stop (×3 = 1.5% for TQQQ/SQQQ) |
| `min_breakout_pct` | `0.001` | Must clear OR high by 0.1% |
| `eod_exit_time` | `"15:45"` | Force close TQQQ/SQQQ |
| `sleeptime_orb` | `"2M"` | 2-min iterations during ORB window |
| `sleeptime_default` | `"5M"` | 5-min iterations rest of day |

---

## Exit logic

Three ways a position closes — whichever fires first:

**1. Trailing stop** — arms after 15 minutes. Ratchets up 2% below the highest price seen. Catches genuine reversals while surviving TQQQ intrabar noise (~0.9–1.5%).

**2. EM boundary exit** (`em_boundary_exit=True`) — if TQQQ reaches the options-implied daily upper boundary, the market has delivered its maximum statistically expected move for the day. Close immediately rather than risk reversal into close. Logged as `EM_TARGET`. Set to `False` to disable.

**3. EOD close at 3:45 PM** — TQQQ/SQQQ always closed before end of day. No overnight holding of leveraged ETFs.

---

## Options Expected Move

Fetched from the QQQ options chain at startup using the ATM straddle.

```
Expected Move  = ATM straddle price × 0.68   (1 standard deviation)
TQQQ/SQQQ EM  = QQQ daily EM × 3             (3× leverage)
```

**Stop floor** — if OR-low produces a stop < `daily_EM / 3`, automatically widens it to avoid noise-triggered exits:
```
Stop widened: OR-low gave ±$0.31 < EM floor ±$1.14 (QQQ EM $0.38 × 3 / 3)
```

**EOD notification** includes expected move context for next session:
```
QQQ expected move: daily ±$3.80 (0.8%) [$496.20–$503.80]
Weekly ±$7.20 [$492.80–$507.20]
TQQQ/SQQQ daily EM: ±$11.40 [$24.60–$47.40]
```

EM features are **live-only** — backtests skip them since historical options data is not available via free yfinance.

---

## Daily schedule

| Time (ET) | Event |
|---|---|
| Script start | Ollama warmup, bias refresh, EM fetch |
| ~9:00 AM | Earnings cache cleared, regime pre-warmed |
| 9:30 AM | Position sync from Alpaca |
| 9:45 AM | ORB entry window opens |
| 9:45 AM – noon | ORB entries evaluated (2-min iterations) |
| Intraday | EM boundary checked every iteration |
| 3:45 PM | TQQQ / SQQQ forced close |
| 3:50 PM | PRELIM EOD signals + next-session EM |
| ~4:05 PM | FINAL EOD signals |

---

## Setup

### Prerequisites

- **Python 3.12** — LumiBot's `numba` requires < 3.14
- **[Ollama](https://ollama.com)** — installed and running locally
- **[Alpaca](https://alpaca.markets)** — cash account (paper or live)

### Install

```bash
ollama pull qwen3:8b

git clone https://github.com/virtualsheng/trading-bot.git
cd trading-bot

py -3.12 -m venv venv
venv\Scripts\activate

pip install lumibot pandas numpy python-dotenv yfinance
pip install alpaca-trade-api alpaca-py requests pytz
```

### Configure and run

```bash
copy .env.example .env   # fill in API keys
start_bot.bat            # launches dashboard + bot
```

---

## Environment variables

```env
# Required
ALPACA_API_KEY=your_key
ALPACA_API_SECRET=your_secret
ALPACA_IS_PAPER=true

# Notifications (all optional)
EMAIL_SENDER=you@gmail.com
EMAIL_PASSWORD=your_app_password
EMAIL_RECIPIENT=you@gmail.com
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## Project structure

```
trading-bot/
├── strategies/
│   ├── ai_engine.py             # Ollama: setup grader, regime detector, narrator
│   ├── earnings_filter.py       # Earnings calendar filter (Yahoo Finance)
│   ├── expected_move.py         # Options implied expected move (QQQ/TQQQ/SQQQ)
│   ├── leverage_map.py          # QQQ → TQQQ / SQQQ mapping
│   ├── premarket_signals.py     # Gap analysis + news enrichment
│   ├── signal_engine.py         # EMA/RSI/MACD/SMA technical signal generator
│   ├── trade_journal.py         # SQLite trade journal
│   └── trend_filtered_orb.py   # ★ Main strategy
│
├── runners/
│   ├── dashboard_server.py      # FastAPI dashboard (port 5001)
│   ├── run_live_combined.py     # ★ Live runner
│   └── run_backtest_combined.py # Backtest runner
│
├── notifications/
│   ├── discord.py
│   ├── emailer.py
│   └── telegram.py
│
├── cache/
│   ├── daily_bias.json          # EOD QQQ signal
│   ├── expected_move_cache.json # Options EM (refreshed daily)
│   └── trade_journal.db
│
├── start_bot.bat                # Launches dashboard + bot
├── check_env.py
├── .env.example
└── README.md
```

---

## Dashboard

Launched automatically by `start_bot.bat` on port 5001.

Tabs: **Overview** · **Positions** · **Trade Log** · **Performance** · **Regime**

---

## Backtest

```bash
python runners/run_backtest_combined.py
```

**2-year results (May 2024 – May 2026):** $2,000 → ~$5,148 (+157%), 67% win rate, 2.78× avg win/loss, 3 losing months out of 25. Trail-only exit, no EM features (not available in backtest).

---

## Risk management

- 1 position max · 2% risk · 40% size cap
- Earnings within 48h: skip entry
- Stop delay 15 min: protects against opening wicks
- Stop floor: EM-based minimum prevents noise exits
- EM boundary exit: locks in gains at statistically maximum daily move
- EOD hard close 3:45 PM: no overnight leveraged ETF exposure

---

## AI layer

**Setup Grader** — Ollama `qwen3:8b` grades each setup on the last 25 five-minute candles. Returns confidence 0.0–1.0 and a size multiplier. Below 0.55 → skip.

**Regime Detector** — every 30 min classifies QQQ as `trending_up`, `trending_down`, `ranging`, `volatile`, `mean_reversion`, or `low_liquidity`. Adjusts distances and skips unfavorable regimes.

**Trade Narrator** — generates a 2–3 sentence journal entry after each close, stored in SQLite.

---

## Known limitations

- Python 3.12 only (`numba` incompatible with 3.13+)
- T+1 cash settlement — funds available next morning
- Ollama cold-start latency ~30–45s
- EM boundary exit skipped in backtest (no historical options data)

---

## Disclaimer

Educational and research purposes only. Not financial advice. Trading leveraged ETFs carries substantial risk. Paper trade extensively before using real money.