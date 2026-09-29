@echo off
rem Starts Mimir elevated: per-process network needs administrator rights.
powershell -NoProfile -Command "Start-Process -FilePath '%~dp0bin\mimir.exe' -Verb RunAs"
