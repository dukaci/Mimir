@echo off
cd /d "%~dp0"
echo ============================================================
echo Mimir CPU Monitor - Standard Mode
echo ============================================================
echo.
echo NOTICE: Per-process network monitoring requires admin privileges.
echo   - For network monitoring: Use run_admin.bat
echo   - CPU monitoring works without admin
echo.
echo Starting Mimir...
echo.
venv\Scripts\python.exe cpu_monitor_gui.py
pause
