@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  start_bot.bat — Trend-Filtered ORB Trading Bot
REM  Single instance: QQQ → TQQQ (bull) / SQQQ (bear)
REM  $2,000 Alpaca cash account, 1 trade per day
REM ─────────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo.
echo  =====================================================
echo   TREND-FILTERED ORB BOT — QQQ INTRADAY
echo  =====================================================
echo   Signal : QQQ
echo   Bull   : TQQQ  (3x Nasdaq bull)
echo   Bear   : SQQQ  (3x Nasdaq bear)
echo   Account: Alpaca cash account (check .env)
echo  =====================================================
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Install Python 3.12.
    pause
    exit /b 1
)

REM Check .env exists
if not exist ".env" (
    echo  ERROR: .env not found. Copy .env.example to .env and fill in credentials.
    pause
    exit /b 1
)

REM Start Ollama if not already running (suppress output)
echo  Starting Ollama...
start /min "" ollama serve >nul 2>&1
timeout /t 3 /nobreak >nul

REM Run the bot
echo  Starting ORB bot...
echo.
python runners\run_live_combined.py

echo.
echo  Bot stopped.
pause