@echo off
title ORB Trading Bot Launcher

:: ── Configuration ────────────────────────────────────────────────────────────
set PROJECT_ROOT=%~dp0
set SENTIMENT_ROOT=C:\Users\sheng\Documents\Sentiment-Trading-Alpha
set PYTHON=python
set DASHBOARD_PORT=5001

:: Sentiment-Trading-Alpha credentials
set ADMIN_API_TOKEN=9kvzQLgoE25NQd1GGGL31N1r7W4hiDBLfQ9XXqByEwHj
set INGESTION_STARTUP_GRACE_SECONDS=20

:: STA internal pipeline timeout in seconds (default is 420 — too short for
:: 0xroyce/plutus:latest on first run). 900 = 15 minutes gives plenty of room.
:: Confirmed env var from STA source: _analysis_timeout_seconds() reads this.
set ANALYSIS_TIMEOUT_SECONDS=900

:: ── Banner ────────────────────────────────────────────────────────────────────
echo.
echo  ================================================
echo    ORB Trading Bot  ^|  v9
echo  ================================================
echo.

:: ── Checks ───────────────────────────────────────────────────────────────────
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found.
    pause & exit /b 1
)

if not exist "%SENTIMENT_ROOT%\run.py" (
    echo  ERROR: Sentiment-Trading-Alpha not found at %SENTIMENT_ROOT%\run.py
    echo  Update SENTIMENT_ROOT at the top of this file.
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

echo.
echo  Starting 3 processes:
echo    1. Sentiment-Trading-Alpha backend  ^(http://localhost:8000^)
echo    2. ORB Dashboard                   ^(http://localhost:%DASHBOARD_PORT%^)
echo    3. ORB Trading Bot
echo.
echo  STA pipeline timeout: %ANALYSIS_TIMEOUT_SECONDS%s
echo  ^(increased from default 420s to handle 0xroyce/plutus on first run^)
echo.

:: ── 1. Start STA backend ─────────────────────────────────────────────────────
:: ANALYSIS_TIMEOUT_SECONDS tells STA's pipeline to wait up to 900s before
:: giving up, instead of the default 420s.
start "Sentiment-Trading-Alpha Backend  [port 8000]" cmd /k "SET ADMIN_API_TOKEN=%ADMIN_API_TOKEN% && SET INGESTION_STARTUP_GRACE_SECONDS=%INGESTION_STARTUP_GRACE_SECONDS% && SET ANALYSIS_TIMEOUT_SECONDS=%ANALYSIS_TIMEOUT_SECONDS% && cd /d %SENTIMENT_ROOT% && %PYTHON% run.py"

echo  Waiting 20 seconds for Sentiment-Trading-Alpha to initialize...
timeout /t 20 /nobreak >nul

:: ── 2. Start dashboard ───────────────────────────────────────────────────────
start "ORB Dashboard  [port %DASHBOARD_PORT%]" cmd /k "%PYTHON% runners\dashboard_server.py"
timeout /t 2 /nobreak >nul

:: ── 3. Start trading bot ─────────────────────────────────────────────────────
start "ORB Trading Bot" cmd /k "%PYTHON% runners\run_live_combined.py"

:: ── Open browser ─────────────────────────────────────────────────────────────
timeout /t 4 /nobreak >nul
start "" "http://localhost:%DASHBOARD_PORT%"

echo.
echo  All 3 processes started.
echo.
echo  Sentiment Alpha : http://localhost:8000
echo  Dashboard       : http://localhost:%DASHBOARD_PORT%
echo.
echo  Press any key to close this launcher.
pause >nul