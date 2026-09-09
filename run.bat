@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Virtual environment missing - run setup.bat first.
    pause
    exit /b 1
)
venv\Scripts\python.exe -m mimir %*
if errorlevel 1 pause
