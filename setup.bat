@echo off
setlocal
cd /d "%~dp0"
echo Mimir setup
echo ===========

set PY=
for %%v in (312 313 311) do (
    if not defined PY if exist "%USERPROFILE%\scoop\apps\python%%v\current\python.exe" set "PY=%USERPROFILE%\scoop\apps\python%%v\current\python.exe"
)
if not defined PY if exist "%USERPROFILE%\scoop\apps\python\current\python.exe" set "PY=%USERPROFILE%\scoop\apps\python\current\python.exe"
if not defined PY (
    py -3 --version >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo No Python 3.10+ found. Install one, e.g.:  scoop install python
    pause
    exit /b 1
)
echo Using: %PY%

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv venv || (echo venv creation failed & pause & exit /b 1)
)

echo Installing dependencies...
venv\Scripts\python.exe -m pip install --upgrade pip >nul
if exist "wheels\pydivert-*.whl" (
    for %%f in (wheels\pydivert-*.whl) do venv\Scripts\python.exe -m pip install "%%f"
)
venv\Scripts\python.exe -m pip install -r requirements.txt || (echo dependency install failed & pause & exit /b 1)

echo Checking...
venv\Scripts\python.exe -c "import psutil, dearpygui.dearpygui; print('ok')" || (echo import check failed & pause & exit /b 1)

echo.
echo Done.  run.bat starts Mimir; run_admin.bat starts it elevated for per-process network stats.
pause
