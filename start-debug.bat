@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title DanLume (debug)

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
%PY% -c "import PySide6" >nul 2>nul
if not errorlevel 1 goto run

echo [DanLume] First run: installing dependencies (PySide6 / requests / cryptography)...
echo [DanLume] This needs internet access and may take a few minutes.
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto depfail

:run
echo [DanLume] Starting in DEBUG mode. A log file is written under data\logs\ .
%PY% main.py --debug %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo [DanLume] The program exited with an error. The traceback is above and also
echo          written to data\logs\ .
echo.
pause
exit /b 1

:depfail
echo.
echo [DanLume] Installing dependencies failed. Copy the messages above when asking for help.
echo.
pause
exit /b 1
