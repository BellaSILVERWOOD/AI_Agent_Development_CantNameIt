@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title DanLume diagnose

rem prefer the py launcher, fall back to python on PATH
set "PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto have_python
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto have_python

echo [DanLume] Python was not found on this computer.
echo   1. Install Python 3.9 or newer: https://www.python.org/downloads/windows/
echo   2. During setup tick "Add python.exe to PATH".
echo   3. Run this file again.
echo.
pause
exit /b 1

:have_python
echo [DanLume] Collecting diagnostics (no GUI window will open)...
%PY% main.py --diagnose --export
echo.
echo [DanLume] A zip bundle was written under data\diagnostics\ .
if exist "data\diagnostics" start "" "data\diagnostics"
echo.
pause
exit /b 0
