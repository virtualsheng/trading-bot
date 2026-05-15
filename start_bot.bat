@echo off
title ORB Trading Bot Launcher

:: ── Configuration ────────────────────────────────────────────────────────────
:: Project root (folder containing runners/, cache/, symbols.txt, etc.)
set PROJECT_ROOT=%~dp0

:: Path to Sentiment-Trading-Alpha repo root (contains run.py)
set SENTIMENT_ROOT=C:\Users\sheng\Documents\Sentiment-Trading-Alpha

:: Python executable
set PYTHON=python

:: Dashboard port
set DASHBOARD_PORT=5001

:: Sentiment-Trading-Alpha credentials
:: Must match SENTIMENT_ADMIN_TOKEN in trading-bot .env
set ADMIN_API_TOKEN=9kvzQLgoE25NQd1GGGL31N1r7W4hiDBLfQ9XXqByEwHj
set INGESTION_STARTUP_GRACE_SECONDS=20

:: ── Banner ────────────────────────────────────────────────────────────────────
echo.
echo  ================================================
echo    ORB Trading Bot  ^|  v8
echo  ================================================
echo.

:: ── Check Python ─────────────────────────────────────────────────────────────
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Make sure Python is in your PATH.
    pause
    exit /b 1
)

:: ── Check Sentiment-Trading-Alpha repo ───────────────────────────────────────
if not exist "%SENTIMENT_ROOT%\run.py" (
    echo  ERROR: Sentiment-Trading-Alpha not found at:
    echo         %SENTIMENT_ROOT%\run.py
    echo.
    echo  Update SENTIMENT_ROOT at the top of this file to the correct path.
    pause
    exit /b 1
)

:: ── Activate trading-bot venv ─────────────────────────────────────────────────
if exist "%PROJECT_ROOT%venv\Scripts\activate.bat" (
    echo  Activating virtual environment...
    call "%PROJECT_ROOT%venv\Scripts\activate.bat"
) else if exist "%PROJECT_ROOT%.venv\Scripts\activate.bat" (
    call "%PROJECT_ROOT%.venv\Scripts\activate.bat"
) else (
    echo  No venv found — using system Python
)

:: ── Change to project root ────────────────────────────────────────────────────
cd /d "%PROJECT_ROOT%"

echo.
echo  Starting 3 processes:
echo    1. Sentiment-Trading-Alpha backend  ^(http://localhost:8000^)
echo    2. ORB Dashboard                   ^(http://localhost:%DASHBOARD_PORT%^)
echo    3. ORB Trading Bot
echo.
echo  Each opens in its own window. Close any window to stop that process.
echo.

:: ── 1. Launch Sentiment-Trading-Alpha backend ─────────────────────────────────
:: Sets env vars only in the new window — does not affect this or other windows.
start "Sentiment-Trading-Alpha Backend  [port 8000]" cmd /k "SET ADMIN_API_TOKEN=%ADMIN_API_TOKEN% && SET INGESTION_STARTUP_GRACE_SECONDS=%INGESTION_STARTUP_GRACE_SECONDS% && cd /d %SENTIMENT_ROOT% && %PYTHON% run.py"

:: ── Wait for STA backend to initialize ───────────────────────────────────────
:: First run: DB migration + ingestion worker startup takes ~10-15s.
:: The trading bot's background thread handles the actual 2-4 min analysis
:: pipeline — the bot doesn't block on it.
echo  Waiting 20 seconds for Sentiment-Trading-Alpha to initialize...
timeout /t 20 /nobreak >nul

:: ── 2. Launch dashboard ───────────────────────────────────────────────────────
start "ORB Dashboard  [port %DASHBOARD_PORT%]" cmd /k "%PYTHON% runners\dashboard_server.py"

timeout /t 2 /nobreak >nul

:: ── 3. Launch trading bot ─────────────────────────────────────────────────────
start "ORB Trading Bot" cmd /k "%PYTHON% runners\run_live_combined.py"

:: ── Open browser ─────────────────────────────────────────────────────────────
timeout /t 4 /nobreak >nul
start "" "http://localhost:%DASHBOARD_PORT%"

echo  All 3 processes started.
echo.
echo  Sentiment Alpha : http://localhost:8000
echo  Dashboard       : http://localhost:%DASHBOARD_PORT%
echo.
echo  Press any key to close this launcher window.
echo  (All processes keep running in their own windows.)
pause >nul