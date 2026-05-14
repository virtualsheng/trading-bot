@echo off
title ORB Trading Bot Launcher

:: ── Configuration ────────────────────────────────────────────────────────────
:: Set this to your project root (the folder containing runners/, cache/, etc.)
:: If you place this .bat file in the project root, leave it as %~dp0
set PROJECT_ROOT=%~dp0

:: Python executable — change to "python3" if needed
set PYTHON=python

:: Dashboard port
set DASHBOARD_PORT=5001

:: ── Banner ────────────────────────────────────────────────────────────────────
echo.
echo  ================================================
echo    ORB Trading Bot  ^|  v5
echo  ================================================
echo.

:: ── Check Python ─────────────────────────────────────────────────────────────
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Make sure Python is in your PATH.
    pause
    exit /b 1
)

:: ── Check venv ────────────────────────────────────────────────────────────────
if exist "%PROJECT_ROOT%venv\Scripts\activate.bat" (
    echo  Activating virtual environment...
    call "%PROJECT_ROOT%venv\Scripts\activate.bat"
) else if exist "%PROJECT_ROOT%.venv\Scripts\activate.bat" (
    call "%PROJECT_ROOT%.venv\Scripts\activate.bat"
) else (
    echo  No venv found — using system Python
)

:: ── Change to project root so relative paths (cache/, symbols.txt) work ──────
cd /d "%PROJECT_ROOT%"

echo  Starting Dashboard on http://localhost:%DASHBOARD_PORT%
echo  Starting Trading Bot...
echo.
echo  Two windows will open — one for each process.
echo  Close this window or press Ctrl+C in either to stop.
echo.

:: ── Launch dashboard in a new window ─────────────────────────────────────────
start "ORB Dashboard  [port %DASHBOARD_PORT%]" cmd /k "%PYTHON% runners\dashboard_server.py"

:: ── Small delay so dashboard starts first ────────────────────────────────────
timeout /t 2 /nobreak >nul

:: ── Launch trading bot in a new window ───────────────────────────────────────
start "ORB Trading Bot" cmd /k "%PYTHON% runners\run_live_combined.py"

:: ── Open browser ─────────────────────────────────────────────────────────────
timeout /t 4 /nobreak >nul
start "" "http://localhost:%DASHBOARD_PORT%"

echo  Both processes started.
echo  Dashboard: http://localhost:%DASHBOARD_PORT%
echo.
echo  Press any key to close this launcher window.
echo  (The bot and dashboard will keep running in their own windows.)
pause >nul