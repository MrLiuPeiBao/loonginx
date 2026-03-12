@echo off
setlocal

set "MODE=%~1"
if "%MODE%"=="" set "MODE=realtime"

if /I "%MODE%"=="help" (
  powershell -ExecutionPolicy Bypass -File "%~dp0env_switch.ps1" -Help
  goto :eof
)
if /I "%MODE%"=="/?" (
  powershell -ExecutionPolicy Bypass -File "%~dp0env_switch.ps1" -Help
  goto :eof
)

powershell -ExecutionPolicy Bypass -File "%~dp0env_switch.ps1" -Mode "%MODE%"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
