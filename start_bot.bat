@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  start_bot.bat — Trend-Filtered ORB Trading Bot
REM  Runs run_live_combined.py directly in a minimized window.
REM  Place in Startup folder to auto-launch on Windows login.
REM ─────────────────────────────────────────────────────────────────────────────

REM Update this path if you move the trading-bot folder
set PROJECT_DIR=C:\Users\sheng\Documents\trading-bot

REM Uncomment to skip weekends (useful in Startup folder)
REM for /f %%d in ('powershell -command "(Get-Date).DayOfWeek"') do set DOW=%%d
REM if "%DOW%"=="Saturday" exit /b
REM if "%DOW%"=="Sunday" exit /b

REM Uncomment to start Ollama (if not already running separately)
REM start /min "" ollama serve

REM Uncomment to start the dashboard on port 5001
REM start /min "ORB Dashboard" cmd /k "cd /d %PROJECT_DIR% && python runners\dashboard_server.py"
REM timeout /t 2 /nobreak >nul
REM start "" "http://localhost:5001"

REM Start the bot — minimized, no extra launcher window
start /min "ORB Bot [QQQ→TQQQ/SQQQ]" cmd /k "cd /d %PROJECT_DIR% && python runners\run_live_combined.py"