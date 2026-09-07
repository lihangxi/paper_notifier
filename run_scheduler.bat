@echo off
setlocal
title paper-notifier scheduler
cd /d "%~dp0"

echo ============================================
echo  paper-notifier daily scheduler
echo  Timezone : see .env (TIMEZONE)
echo  Run time : see .env (RUN_TIME)
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found: .venv\Scripts\python.exe
    echo Run: py -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo [paper-notifier] Starting scheduler... (keep this window open)
echo.
".venv\Scripts\python.exe" -m paper_notifier.cli --schedule
set EXIT_CODE=%errorlevel%

echo.
if "%EXIT_CODE%"=="0" (
    echo [paper-notifier] Scheduler stopped cleanly.
) else (
    echo [paper-notifier] Scheduler exited with code %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%
