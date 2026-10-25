@echo off
setlocal
rem Remove the "7z Hash Compare" entry from the SendTo menu.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall-sendto.ps1"
echo.
pause
endlocal
