@echo off
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% == 0 goto :run
echo Requesting administrator rights (needed only for per-process network stats)...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:run
if not exist "venv\Scripts\python.exe" (
    echo Virtual environment missing - run setup.bat first.
    pause
    exit /b 1
)
venv\Scripts\python.exe -m mimir %*
if errorlevel 1 pause
