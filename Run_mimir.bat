@echo off
REM Mimir CPU Monitor - Quick Launch
REM Auto-setup if needed, then run GUI immediately

echo Mimir CPU Monitor
echo =================

REM Check if virtual environment exists
if not exist "venv\Scripts\python.exe" (
    echo Virtual environment not found. Running setup...
    echo.
    call setup.bat
    if errorlevel 1 (
        echo Setup failed. Please check the error messages above.
        pause
        exit /b 1
    )
)

REM Check if dependencies are installed
venv\Scripts\python.exe -c "import psutil; import matplotlib" 2>nul
if errorlevel 1 (
    echo Dependencies missing. Running setup...
    echo.
    call setup.bat
    if errorlevel 1 (
        echo Setup failed. Please check the error messages above.
        pause
        exit /b 1
    )
)

REM All good - launch GUI
echo Starting Mimir CPU Monitor GUI...
echo.
venv\Scripts\python.exe cpu_monitor_gui.py

if errorlevel 1 (
    echo.
    echo GUI failed to start. Check error messages above.
    pause
    exit /b 1
)
