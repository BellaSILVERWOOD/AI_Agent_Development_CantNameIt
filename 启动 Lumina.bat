@echo off
setlocal
cd /d "%~dp0"

rem 优先使用 py 启动器，其次使用 python
set "PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto have_python

where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto have_python

echo [Lumina] 未找到 Python，请先安装 Python 3.9+ 并勾选 "Add Python to PATH"
pause
exit /b 1

:have_python
%PY% -c "import PySide6" >nul 2>nul
if not errorlevel 1 goto launch

echo [Lumina] 首次运行，正在安装 PySide6，请稍候...
%PY% -m pip install -r "requirements.txt"
if errorlevel 1 goto pip_failed

:launch
where pythonw >nul 2>nul
if errorlevel 1 (
    start "" %PY% "main.py"
) else (
    start "" pythonw "main.py"
)
exit /b 0

:pip_failed
echo [Lumina] 依赖安装失败，请手动执行：
echo     python -m pip install -r requirements.txt
pause
exit /b 1
