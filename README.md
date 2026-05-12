# Technical Trading Bot

AI-Enhanced Technical Momentum Trading System for Alpaca + Leveraged ETFs

---

## Overview

This project is an automated technical trading system.

The goal of this project is to systematically automate:

- Morning Opening Range Breakout (ORB) execution
- End-of-day technical bias analysis
- Leveraged ETF directional exposure
- Dynamic position sizing
- AI-assisted trade grading
- Market regime detection
- Trade journaling and future machine learning training

---

# Core Trading Philosophy

This system is NOT intended to blindly predict markets.

Instead, it combines:

1. Deterministic technical analysis
2. Structured risk management
3. AI-assisted probability weighting
4. Market regime awareness
5. Sentiment/context filtering (future integration with Jeff's Sentiment Trading Alpha https://github.com/techjeffe/Sentiment-Trading-Alpha)

The strategy architecture mirrors how experienced discretionary traders operate:

- Technical structure identifies opportunity
- Market context determines conviction
- Risk sizing adjusts exposure
- AI acts as a quality filter

---

# Current Strategy Architecture

## TrendFilteredORB Strategy

The current live strategy combines two independent signal engines:

### 1. Previous-Day Technical Bias Engine

Executed near market close (~3:50pm ET)

Uses:
- EMA structure
- RSI
- MACD
- Volume analysis
- Trend confirmation

This establishes the directional bias for the next trading day.

Example:

| Technical Signal | Directional Bias |
|---|---|
| BUY / STRONG_BUY | Bullish |
| SELL / STRONG_SELL | Bearish |
| HOLD | Neutral |

---

### 2. Morning ORB (Opening Range Breakout)

Executed after market open.

Uses:
- 15-minute opening range
- 5-minute candle confirmations
- Volume expansion
- Intraday breakout structure

The ORB engine provides tactical entries aligned with the broader technical bias.

---

# IMPORTANT: Leveraged ETF Logic

This strategy DOES NOT short ETFs.

Instead:

| Market View | Action |
|---|---|
| Bullish Nasdaq | BUY TQQQ |
| Bearish Nasdaq | BUY SQQQ |

This distinction is critical.

The system never intentionally opens:
- short TQQQ
- short SQQQ
- naked short positions

All directional exposure is expressed through leveraged inverse ETFs.

---

# Trade Flow

```text
3:50pm ET
    ↓
Technical Analysis Engine
    ↓
Store Bias Cache
    ↓
Next Morning
    ↓
ORB Signal Detection
    ↓
Bias Confirmation
    ↓
AI Confidence Grading
    ↓
Dynamic Position Sizing
    ↓
Alpaca Execution
    ↓
Trade Journaling
```

---

# HOLD Override Logic

If:
- previous-day technical bias = HOLD
- AND a strong ORB breakout occurs

then:
- the bot may still enter a position
- but at reduced size

This allows tactical momentum trades while respecting broader market uncertainty.

Example:

| Condition | Position Size |
|---|---|
| Strong bias + strong ORB | Full size |
| HOLD bias + strong ORB | Half size |
| Weak confidence | Reduced size |
| Low confidence | Skip trade |

---

# Current Features

## Technical Indicators

- EMA trend structure
- RSI momentum analysis
- MACD crossover analysis
- Volume expansion detection
- ORB breakout detection
- Relative strength confirmation

---

## Risk Management

- Max simultaneous positions
- Risk-per-trade limits
- Dynamic stop losses
- Reward:risk targeting
- Intraday exposure control

---

## Notifications

Integrated alerting via:
- Email
- Discord
- Telegram

---

## Alpaca Integration

Broker:
- Alpaca Paper Trading
- Alpaca Live Trading

Supports:
- Automated entries
- Automated exits
- Position monitoring
- Real-time execution

---

# AI / LLM Roadmap (Ollama Integration)

This project is being expanded with local AI/LLM analysis using:

- Ollama
- Qwen
- Llama
- Future local models

AI is NOT intended to replace technical analysis.

Instead, AI acts as:

- Trade quality evaluator
- Probability estimator
- Market regime classifier
- Confidence scoring engine

---

# AI-Powered Setup Grading

Before entering a trade:

The system will send recent OHLC candles to an LLM.

Example prompt:

> "Is this breakout occurring on increasing volume? Is price tightly coiling or already overextended? Does this look like a high-quality momentum breakout?"

The AI returns:

```json
{
  "confidence": 0.82,
  "reason": "Strong volume expansion with tight consolidation breakout."
}
```

---

# AI Confidence-Based Position Sizing

| AI Confidence | Setup Quality | Position Size |
|---|---|---|
| 0.95+ | Elite | 2.0x |
| 0.80+ | Strong | 1.0x |
| 0.65+ | Acceptable | 0.5x |
| < 0.55 | Weak | Skip Trade |

This creates adaptive risk exposure based on setup quality.

---

# Dynamic Market Regime Detection

The AI engine will classify current market conditions into regimes such as:

- Trending
- Mean Reversion
- Choppy
- Panic Selling
- Volatility Expansion
- Low Liquidity
- Momentum Continuation

The strategy can then adapt:

| Regime | Strategy Behavior |
|---|---|
| Strong Trend | Increase exposure |
| Choppy Market | Reduce ORB trades |
| Mean Reversion | Tighten targets |
| High Volatility | Reduce sizing |

---

# Trade Journal Database

Full SQLite trade journal.

Tracked fields:

- Entry
- Exit
- Signal Type
- AI Confidence
- Market Regime
- Volatility Context
- Sentiment Score
- Win/Loss
- R-Multiple
- Trade Duration
- AI Narrative Summary

Purpose:
- Performance analysis
- Confidence validation
- Future ML training
- Regime optimization

---

# AI Fusion Architecture

System architecture:

```text
                         ┌────────────────┐
                         │ Technical Bot │
                         │ ORB/EMA/RSI   │
                         └──────┬─────────┘
                                │
                                ▼
                        Candidate Trade
                                │
                                ▼
                       ┌─────────────────┐
                       │ AI Fusion Layer │
                       │ Confidence Rank │
                       └──────┬──────────┘
                              │
        ┌─────────────────────┴─────────────────────┐
        ▼                                           ▼

 Sentiment Engine                              Regime Engine
 (Jeff's Bot)                                 Volatility/Trend

        │                                           │
        └─────────────────────┬─────────────────────┘
                              ▼

                     Final Probability Score
                              ▼

                     Dynamic Position Sizing
                              ▼

                       Alpaca Execution
```

---

# Future Integration with Sentiment-Trading-Alpha

Future development will align closely with:

https://github.com/techjeffe/Sentiment-Trading-Alpha

The long-term goal is to combine:

| Technical Engine | Sentiment Engine |
|---|---|
| Price structure | News sentiment |
| ORB timing | Narrative momentum |
| EMA/RSI/MACD | Crowd psychology |
| Momentum signals | AI sentiment scoring |
| Technical breakouts | Macro context |

This creates a hybrid:
- technical
- sentiment
- AI-assisted
- probability-weighted

trading framework.

---

# Project Structure

```text
technical-bot/
│
├── core/
│   ├── orb.py
│   ├── leverage_map.py
│
├── strategies/
│   ├── trend_filtered_orb.py
│   ├── signal_engine.py
│   ├── ai_engine.py
│   ├── trade_journal.py
│
├── runners/
│   ├── run_live_combined.py
│   ├── run_orb_check.py
│   ├── run_technical_signals.py
│
├── notifications/
│   ├── discord.py
│   ├── emailer.py
│   ├── telegram.py
│
├── cache/
├── logs/
├── symbols.txt
├── .env
└── README.md
```

---

# Important Notes

This project is experimental and educational.

Leveraged ETFs are highly volatile and involve substantial risk.

This repository is NOT financial advice.

Always:
- paper trade first
- validate strategies thoroughly
- use proper risk management
- understand leveraged ETF decay
- understand execution risks

---

# Current Development Priorities

## Near-Term

- Validate ETF execution logic
- Add duplicate order protection
- Improve Alpaca reconciliation
- Validate AI confidence engine
- Validate SQLite trade journal
- Fix backtesting framework

---

## Mid-Term

- Validate Multi-timeframe AI reasoning
- Validate Volatility regime detection
- Validate Position scaling logic
- Validate Confidence-weighted entries
- Validate Trade narrative generation

---

## Long-Term

- Plan Sentiment + Technical AI Fusion
- Reinforcement learning experiments
- Ensemble AI models
- Portfolio-level optimization
- Autonomous adaptive strategies

---

# Disclaimer

This software is for educational and research purposes only.

Trading leveraged ETFs involves substantial financial risk.

Use at your own risk.