@echo off
setlocal
cd /d "%~dp0"

set "PY="
where pythonw >nul 2>nul && set "PY=pythonw"
if not defined PY where pyw >nul 2>nul && set "PY=pyw"
if not defined PY where python >nul 2>nul && set "PY=python"

if not defined PY (
    if exist "D:\ProgramFiles\python\pythonw.exe" set "PY=D:\ProgramFiles\python\pythonw.exe"
)
if not defined PY (
    echo.
    echo   Python not found in PATH.
    echo   Please install Python 3.9+ ^(tkinter included^) and try again.
    echo.
    pause
    exit /b 1
)

start "" "%PY%" "%~dp0main.py" %*
exit /b 0
