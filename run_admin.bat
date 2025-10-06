@echo off
cd /d "%~dp0"

REM Check if already running as administrator
net session >nul 2>&1
if %errorlevel% == 0 (
    echo Already running with administrator privileges.
    goto :run_program
)

REM Not admin - request elevation
echo Requesting administrator privileges...
echo You will see a UAC prompt - click "Yes" to continue.
echo.

REM Use PowerShell to elevate and run the batch file
powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:run_program
echo ============================================================
echo Mimir CPU Monitor - Administrator Mode
echo ============================================================
echo.
echo Per-process network monitoring will be ENABLED.
echo Using WinDivert (NETWORK layer) + psutil connection mapping.
echo.

REM Run the Python script
venv\Scripts\python.exe cpu_monitor_gui.py

echo.
echo Mimir has exited.
pause
