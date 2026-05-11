# Technical Trading Bot (Morning / Afternoon)

- Morning **Opening Range Breakout (ORB)**
- Afternoon **Momentum + EMA crossover + Trend signals**

## Features

- Two daily alerts (9:45 AM & 3:50 PM ET)
- ORB on 5-min chart (first 15 minutes)
- Short-term momentum using 2/3 and 3/5 EMAs
- Trend confirmation with SMA50/SMA200 + RSI + MACD
- Multiple notification channels (Email, Discord, Telegram)
- yfinance fallback (works even on free Alpaca)

## Folder Structure

technical-bot/
├── alerts/                  # Daily runnable scripts
│   ├── run_orb_check.py
│   └── run_technical_signals.py
├── core/                    # Core logic
│   ├── data.py
│   ├── indicators.py
│   └── orb.py
├── strategies/              # Signal engines
│   ├── signal_engine.py
│   └── orb_strategy.py
├── notifications/
├── runners/                 # Lumibot backtesting
└── .env (copy from example.env)

## Setup

1. `python -m venv venv`
2. `venv\Scripts\activate` (Windows) or `source venv/bin/activate`
3. `pip install -r requirements.txt`
4. Copy `example.env` → `.env` and fill in your keys
5. (Recommended) Use Gmail **App Password** for email

## Usage

**Morning Alert (ORB):**
```bash
python alerts/run_orb_check.py
```

**Afternoon Alert (Technical):**
```bash
python alerts/run_technical_signals.py
```
