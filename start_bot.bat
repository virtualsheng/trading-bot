@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  start_bot.bat — Trend-Filtered ORB Trading Bot
REM  Launches: Dashboard (port 5001) + ORB bot
REM  Signal: QQQ → TQQQ (bull) / SQQQ (bear)
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

REM ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Install Python 3.12.
    pause
    exit /b 1
)

REM ── Check .env ────────────────────────────────────────────────────────────────
if not exist ".env" (
    echo  ERROR: .env not found. Copy .env.example to .env and fill in credentials.
    pause
    exit /b 1
)

REM ── Validate .env keys ────────────────────────────────────────────────────────
python check_env.py
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)

REM ── Start Ollama ──────────────────────────────────────────────────────────────
echo  Starting Ollama...
start /min "Ollama" ollama serve
timeout /t 3 /nobreak >nul

REM ── Start Dashboard ───────────────────────────────────────────────────────────
echo  Starting Dashboard on http://localhost:5001 ...
start "ORB Dashboard  [http://localhost:5001]" cmd /k "python runners\dashboard_server.py"
timeout /t 2 /nobreak >nul

REM ── Open browser ─────────────────────────────────────────────────────────────
start "" "http://localhost:5001"

REM ── Start ORB Bot ─────────────────────────────────────────────────────────────
echo  Starting ORB bot...
echo.
start "ORB Bot  [QQQ→TQQQ/SQQQ]" cmd /k "python runners\run_live_combined.py"

echo.
echo  =====================================================
echo   Both processes started:
echo     Dashboard : http://localhost:5001
echo     Bot       : see "ORB Bot" window
echo.
echo   Logs : logs\bot_YYYYMMDD_HHMMSS.log
echo   Cache: cache\daily_bias.json
echo          cache\trade_journal.db
echo  =====================================================
echo.
echo  Close this window when done, or Ctrl+C in each
echo  window to stop dashboard / bot individually.
pause