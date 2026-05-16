# trading-bot

AI-enhanced algorithmic trading bot for US equities. Trades **QQQ only** — buys TQQQ (3× bull) on BUY signals and SQQQ (3× bear) on SELL signals. Runs on a $2,000 Alpaca cash account with one trade per day.

> **Swing trading for retirement accounts** has moved to the separate [`swing_signal_engine/`](../swing_signal_engine) project.

---

## How it works

```
EOD Technical Signal (EMA/RSI/MACD/SMA)
  ↓  stored in cache/daily_bias.json
Morning ORB Breakout (9:45 AM — if aligns with bias)
  ↓
Ollama AI Setup Grader → confidence score + size multiplier
  ↓
Regime Detector (trending / ranging / volatile)
  ↓
Position entry: BUY TQQQ or BUY SQQQ
  ↓
Trailing stop (2%) ratchets up as price rises
  ↓
Exit: trail fires OR EOD close at 3:45 PM
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
| `reward_ratio` | `2.0` | 2:1 reference target |
| `ai_min_confidence` | `0.55` | Minimum AI confidence to enter |
| `min_stop_pct` | `0.005` | Floor stop (×3 = 1.5% for TQQQ/SQQQ) |
| `min_breakout_pct` | `0.001` | Must clear OR high by 0.1% |
| `eod_exit_time` | `"15:45"` | Force close TQQQ/SQQQ |
| `sleeptime_orb` | `"2M"` | 2-min iterations during ORB window |
| `sleeptime_default` | `"5M"` | 5-min iterations rest of day |

---

## Exit logic

No hard target exit. The trade rides until one of two things happens:

1. **Trailing stop fires** — stop ratchets up 2% below the highest price seen since entry. Catches genuine reversals while surviving normal TQQQ intrabar noise (~0.9–1.5%).
2. **EOD close at 3:45 PM** — TQQQ/SQQQ are always closed before end of day. No overnight holding of leveraged ETFs.

When price passes the initial target it logs: `TARGET PASSED TQQQ @ 34.56 | unrealised PnL: +$54.24 | trail=2.0% — letting it ride`

---

## Daily schedule

| Time (ET) | Event |
|---|---|
| Script start | Ollama warmup, bias refresh |
| ~9:00 AM | Earnings cache cleared, regime pre-warmed |
| 9:30 AM | Position sync from Alpaca |
| 9:45 AM | ORB entry window opens (2-min iterations) |
| 9:45 AM – noon | ORB entries evaluated |
| 3:45 PM | TQQQ / SQQQ forced close |
| 3:50 PM | PRELIM EOD signals |
| ~4:05 PM | FINAL EOD signals via `after_market_closes()` |

---

## Setup

### Prerequisites

- **Python 3.12** — LumiBot's `numba` dependency requires < 3.14
- **[Ollama](https://ollama.com)** — installed and running locally
- **[Alpaca](https://alpaca.markets)** — cash account (paper or live)

### Install Ollama model

```bash
ollama pull qwen3:8b
```

### Clone and install

```bash
git clone https://github.com/virtualsheng/trading-bot.git
cd trading-bot

py -3.12 -m venv venv
venv\Scripts\activate        # Windows

pip install --upgrade pip
pip install lumibot pandas numpy python-dotenv yfinance
pip install alpaca-trade-api alpaca-py requests pytz
```

### Configure .env

```bash
copy .env.example .env
# Fill in your Alpaca API keys and notification credentials
```

### Run

```bat
start_bot.bat
```

Or manually:

```bash
python runners/run_live_combined.py
```

---

## Environment variables

See `.env.example` for the full template. Key variables:

```env
ALPACA_API_KEY=your_key
ALPACA_API_SECRET=your_secret
ALPACA_IS_PAPER=true          # true = paper trading, false = live

EMAIL_SENDER=you@gmail.com
EMAIL_PASSWORD=your_app_password
EMAIL_RECIPIENT=you@gmail.com

TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## Project structure

```
trading-bot/
│
├── strategies/
│   ├── ai_engine.py             # Ollama: setup grader, regime detector, narrator
│   ├── earnings_filter.py       # Earnings calendar filter (Yahoo Finance)
│   ├── leverage_map.py          # QQQ → TQQQ/SQQQ mapping
│   ├── premarket_signals.py     # Gap analysis + news enrichment
│   ├── signal_engine.py         # EMA/RSI/MACD/SMA technical signal generator
│   ├── trade_journal.py         # SQLite trade journal
│   └── trend_filtered_orb.py   # ★ Main strategy
│
├── runners/
│   ├── run_live_combined.py     # ★ Live runner
│   └── run_backtest_combined.py # Backtest runner
│
├── notifications/
│   ├── discord.py               # Discord webhook
│   ├── emailer.py               # SMTP email
│   └── telegram.py              # Telegram bot
│
├── cache/
│   ├── daily_bias.json          # EOD signal cache
│   └── trade_journal.db         # Trade journal (SQLite)
│
├── logs/
│   └── bot_YYYYMMDD_HHMMSS.log
│
├── start_bot.bat                # One-click Windows launcher
├── .env                         # API keys — never commit
├── .env.example                 # Template
└── README.md
```

---

## Notifications

All notifications use the subject prefix `Trade-Bot:` for easy email filtering.

Fires on: trade entry, trade exit (with P&L), EOD signal summary.

| Channel | Variable |
|---|---|
| Email | `EMAIL_SENDER`, `EMAIL_PASSWORD`, `EMAIL_RECIPIENT` |
| Discord | `DISCORD_WEBHOOK_URL` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |

---

## Backtest

```bash
python runners/run_backtest_combined.py
```

Configured for $2,000 starting capital, QQQ → TQQQ/SQQQ, Mar–May 2025 by default (tariff crash + Iran War volatility period). Edit `START`, `END`, and `STARTING_CAPITAL` in `run_backtest_combined.py` to change the period.

**2-year backtest results (May 2024 – May 2026):**
- Starting capital: $2,000
- Ending value: ~$5,148
- Total return: +157%
- Win rate: 67% (177/263 trades)
- Avg win / avg loss ratio: 2.78×
- Only 3 losing months out of 25

---

## Risk management

- Maximum 1 position at a time (QQQ only)
- 2% risk per trade (~$40 on $2,000)
- 40% maximum position size (~$800)
- Earnings within 48 hours: skip entry
- Stop inactive first 15 min after entry (protects against stop-hunt wicks)
- TQQQ/SQQQ always closed at 3:45 PM — no overnight holding of leveraged ETFs
- One entry per day — re-entry blocked after STOP or target passed

---

## Known limitations

- **Python 3.12 only** — `numba` (required by LumiBot) does not support 3.13+
- **Cash account T+1 settlement** — funds available next morning after close
- **Ollama first-call latency** — qwen3:8b takes ~30–45s to load on cold start

---

## Disclaimer

For educational and research purposes only. Not financial advice. Trading leveraged ETFs carries substantial risk. Paper trade extensively before using real money. Past performance does not guarantee future results.