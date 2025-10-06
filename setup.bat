@echo off
REM CPU Monitor Setup - Self-Contained Project Version
REM Creates a virtual environment with all dependencies

echo CPU Monitor Setup - Self-Contained Project
echo ===========================================

REM Function to find suitable Python
set PYTHON_FOUND=0
set PYTHON_EXE=

REM Check for Scoop Python first (best option)
if exist "C:\Users\%USERNAME%\scoop\apps\python\current\python.exe" (
    set PYTHON_EXE=C:\Users\%USERNAME%\scoop\apps\python\current\python.exe
    set PYTHON_FOUND=1
    echo Found Scoop Python (recommended)
    goto :create_venv
)

REM Check default Python in PATH
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_EXE=python
    set PYTHON_FOUND=1
    echo Found Python in PATH
    goto :create_venv
)

REM If no Python found
if %PYTHON_FOUND%==0 (
    echo ERROR: No suitable Python installation found
    echo.
    echo RECOMMENDED SOLUTIONS:
    echo 1. Install Python via Scoop: scoop install python
    echo 2. Install Python from python.org
    echo 3. Install Python from Microsoft Store
    echo.
    pause
    exit /b 1
)

:create_venv
echo Using Python: %PYTHON_EXE%
%PYTHON_EXE% --version

echo.
echo Step 1: Creating virtual environment...

REM Remove existing venv if it exists
if exist "venv" (
    echo Removing existing virtual environment...
    rmdir /s /q venv
)

REM Create new venv
echo Creating new virtual environment...
%PYTHON_EXE% -m venv venv
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment
    echo Make sure you have a working Python installation
    pause
    exit /b 1
)

echo.
echo Step 2: Installing dependencies...

REM Install core packages in venv
echo Installing psutil, matplotlib, dearpygui, and pywin32...
venv\Scripts\pip.exe install psutil matplotlib dearpygui pywin32
if errorlevel 1 (
    echo ERROR: Failed to install core packages
    echo.
    echo Try running this manually:
    echo   venv\Scripts\activate
    echo   pip install psutil matplotlib dearpygui pywin32
    pause
    exit /b 1
)

REM Install pydivert from local wheel if available, otherwise from PyPI
echo.
if exist "wheels\pydivert-*.whl" (
    echo Installing pydivert from local wheel...
    for %%f in (wheels\pydivert-*.whl) do (
        venv\Scripts\pip.exe install "%%f"
    )
) else (
    echo Installing pydivert from PyPI...
    venv\Scripts\pip.exe install pydivert
)

if errorlevel 1 (
    echo WARNING: Failed to install pydivert
    echo Network monitoring will not be available until pydivert is installed
    echo You can install it later from the GUI
)

echo.
echo Step 3: Testing installation...
venv\Scripts\python.exe -c "import psutil; import matplotlib; import dearpygui.dearpygui; print('SUCCESS: All packages working!')"
if errorlevel 1 (
    echo WARNING: Package import test failed
)

:show_usage
echo.
echo ================================================
echo SETUP COMPLETE!
echo ================================================
echo.
echo Your CPU Monitor is now self-contained with its own virtual environment.
echo.
echo To run the CPU Monitor:
echo.
echo GUI Version - DearPyGui (Recommended - GPU Accelerated):
echo   venv\Scripts\python.exe cpu_monitor_gui.py
echo   Or double-click: run.bat
echo.
echo Command Line Version:
echo   venv\Scripts\python.exe cpu_monitor.py
echo.
echo Or use the interactive launcher:
echo   choose_version.bat
echo.
echo Configuration:
echo   Edit config.ini to customize settings
echo.
echo The project is completely self-contained and portable!
echo.
pause