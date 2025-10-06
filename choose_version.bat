@echo off
cd /d "%~dp0"
REM CPU Monitor Version Chooser
REM Interactive menu to choose which version to run

echo CPU Monitor - Choose Version
echo ==============================

REM Check if virtual environment exists
if not exist "venv\Scripts\python.exe" (
    echo Virtual environment not found!
    echo Please run setup.bat first to create the environment.
    echo.
    pause
    exit /b 1
)

REM Check if dependencies are installed
echo Checking dependencies...
venv\Scripts\python.exe -c "import psutil; import matplotlib" 2>nul
if errorlevel 1 (
    echo Dependencies not found in virtual environment!
    echo Please run setup.bat to install dependencies.
    echo.
    pause
    exit /b 1
)

REM Show menu
:menu
echo.
echo Choose version to run:
echo 1. GUI Version - DearPyGui (Recommended - GPU Accelerated)
echo 2. Command Line Version
echo 3. Exit
echo.
set /p choice="Enter your choice (1-3): "

if "%choice%"=="1" (
    echo Starting DearPyGui version (GPU-accelerated)...
    venv\Scripts\python.exe cpu_monitor_gui.py
    if errorlevel 1 (
        echo.
        echo GUI version failed to start. Check error messages above.
        pause
    )
) else if "%choice%"=="2" (
    echo Starting command line version...
    venv\Scripts\python.exe cpu_monitor.py
    if errorlevel 1 (
        echo.
        echo CLI version failed to start. Check error messages above.
        pause
    )
) else if "%choice%"=="3" (
    goto end
) else (
    echo Invalid choice. Please try again.
    goto menu
)

goto menu

:end
echo.
echo Thanks for using CPU Monitor!
pause
