@echo off
rem ============================================================
rem  paper-notifier background scheduler worker.
rem  Normally started hidden by scheduler_control.vbs
rem  Can also be double-clicked to run in a console for debugging;
rem  all app output is appended to logs\scheduler.log either way.
rem ============================================================
setlocal
cd /d "%~dp0"

set "LOG=logs\scheduler.log"
if not exist "logs" mkdir "logs"

set "HIDDEN="
if /i "%~1"=="hidden" set "HIDDEN=1"

if not exist ".venv\Scripts\python.exe" goto no_venv

call :say "scheduler starting, output goes to %LOG%"
call :say "started at %date% %time%"

if defined PYTHONPATH (
    set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%~dp0src"
)
rem Unbuffered output so every log line appears in the file immediately.
set "PYTHONUNBUFFERED=1"

".venv\Scripts\python.exe" -m paper_notifier.cli --schedule >> "%LOG%" 2>&1
set "EXIT_CODE=%errorlevel%"

call :say "scheduler stopped at %date% %time%, exit code %EXIT_CODE%"

if not defined HIDDEN (
    echo.
    echo [paper-notifier] Scheduler exited with exit code %EXIT_CODE%.
    echo [paper-notifier] Full output: %~dp0%LOG%
    pause
)
exit /b %EXIT_CODE%

:no_venv
call :say "[ERROR] virtual environment not found at .venv\Scripts\python.exe"
call :say "create it, then install dependencies: pip install -r requirements.txt"
if not defined HIDDEN (
    echo.
    echo [paper-notifier] Virtual environment not found: .venv\Scripts\python.exe
    echo [paper-notifier] Create it with:
    echo     py -m venv .venv
    echo     .venv\Scripts\python -m pip install -r requirements.txt
    pause
)
exit /b 1

:say
echo [paper-notifier] %~1
>>"%LOG%" echo [paper-notifier] %~1
goto :eof
