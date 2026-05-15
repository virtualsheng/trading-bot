@echo off
title ORB Trading Bot Launcher — Dual Account

:: ── Configuration ────────────────────────────────────────────────────────────
set PROJECT_ROOT=%~dp0
set PYTHON=python
set DASHBOARD_PORT=5001

:: ── Banner ────────────────────────────────────────────────────────────────────
echo.
echo  ====================================================
echo    ORB Trading Bot  ^|  v10  ^|  Dual Account
echo  ====================================================
echo.

:: ── Checks ───────────────────────────────────────────────────────────────────
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Make sure Python is in your PATH.
    pause & exit /b 1
)

:: ── Activate venv ─────────────────────────────────────────────────────────────
if exist "%PROJECT_ROOT%venv\Scripts\activate.bat" (
    call "%PROJECT_ROOT%venv\Scripts\activate.bat"
) else if exist "%PROJECT_ROOT%.venv\Scripts\activate.bat" (
    call "%PROJECT_ROOT%.venv\Scripts\activate.bat"
) else (
    echo  No venv found — using system Python
)

cd /d "%PROJECT_ROOT%"

:: ── Validate .env keys ────────────────────────────────────────────────────────
%PYTHON% check_env.py
if errorlevel 1 (
    echo.
    pause & exit /b 1
)

echo.
echo  Starting 3 processes:
echo    1. ORB Dashboard                   ^(http://localhost:%DASHBOARD_PORT%^)
echo    2. ORB account   ^(day trade,  swing_mode=false^)
echo    3. SWING account ^(overnight,  swing_mode=true^)
echo.
echo  Signals: EMA/RSI/MACD + Gap analysis + Alpaca News
echo  ^(Sentiment-Trading-Alpha removed — see README for details^)
echo.

:: ── 1. Start dashboard ───────────────────────────────────────────────────────
start "ORB Dashboard  [port %DASHBOARD_PORT%]" cmd /k "%PYTHON% runners\dashboard_server.py"
timeout /t 2 /nobreak >nul

:: ── 2. Start ORB account (day trade, swing_mode=false) ───────────────────────
start "ORB Account  [day trade]" cmd /k "%PYTHON% runners\run_live_combined.py --account orb"

:: ── 3. Start SWING account (overnight, swing_mode=true) ──────────────────────
:: Delay so ORB warms up Ollama first — both share the same instance
echo  Waiting 15 seconds before starting SWING account ^(Ollama warmup^)...
timeout /t 15 /nobreak >nul
start "SWING Account  [overnight]" cmd /k "%PYTHON% runners\run_live_combined.py --account swing"

:: ── Open browser ─────────────────────────────────────────────────────────────
timeout /t 5 /nobreak >nul
start "" "http://localhost:%DASHBOARD_PORT%"

echo.
echo  All 3 processes started.
echo.
echo  Dashboard : http://localhost:%DASHBOARD_PORT%
echo.
echo  Logs:
echo    ORB   : logs\bot_orb_YYYYMMDD_HHMMSS.log
echo    SWING : logs\bot_swing_YYYYMMDD_HHMMSS.log
echo.
echo  Cache:
echo    ORB   bias    : cache\daily_bias_orb.json
echo    SWING bias    : cache\daily_bias_swing.json
echo    ORB   journal : cache\trade_journal_orb.db
echo    SWING journal : cache\trade_journal_swing.db
echo.
echo  Press any key to close this launcher.
pause >nul