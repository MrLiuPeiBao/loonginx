@echo off
setlocal
chcp 65001 >nul

echo [stop_all] stopping server stack and nginx...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_all.ps1"
pause
endlocal
