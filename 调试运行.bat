@echo off
setlocal
cd /d "%~dp0"

set "PY=python"
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"

%PY% "main.py"
echo.
echo [Lumina] 已退出，按任意键关闭窗口...
pause >nul
