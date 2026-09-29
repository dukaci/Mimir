@echo off
rem Builds Mimir (Release) into bin\ with the Visual Studio 2022 Build Tools, which bring cmake and ninja.
setlocal
cd /d "%~dp0"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -property installationPath`) do set "VS=%%i"
if not defined VS (echo Visual Studio Build Tools not found & exit /b 1)
where cl >nul 2>&1 || call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
set "PATH=%VS%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;%VS%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
if not exist build\CMakeCache.txt cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release || exit /b 1
cmake --build build || exit /b 1
bin\mimir_tests.exe || exit /b 1
echo Done. bin\mimir.exe starts Mimir; run_admin.bat starts it elevated for per-process network.
