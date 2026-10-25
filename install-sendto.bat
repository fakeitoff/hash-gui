@echo off
setlocal
rem Add "7z Hash Compare" to the Explorer SendTo menu (current user only, no registry).
rem To remove it later, run uninstall-sendto.bat
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-sendto.ps1"
echo.
pause
endlocal
