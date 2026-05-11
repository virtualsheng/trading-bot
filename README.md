
# Technical Bot

Refactored architecture separating:
- Alert scanners
- Technical analysis engine
- ORB engine
- Notifications
- Lumibot strategies

## Folder Structure

core/
    data.py
    indicators.py
    orb.py

strategies/
    signal_engine.py
    orb_strategy.py

alerts/
    run_orb_check.py
    run_technical_signals.py

notifications/
    emailer.py
    discord.py
    telegram.py

## Alert Schedules

Morning ORB:
9:45 AM ET

Afternoon Technical Signals:
3:50 PM ET

## Setup

1. Create virtual environment
2. Install requirements
3. Configure .env
4. Run alert scripts

Example:

python alerts/run_orb_check.py
python alerts/run_technical_signals.py
