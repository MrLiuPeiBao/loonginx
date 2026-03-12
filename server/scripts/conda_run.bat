@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:\=/%"

set "ENV_NAME=%~1"
if "%ENV_NAME%"=="" set "ENV_NAME=sensor_server"

set "MODE=%~2"
if "%MODE%"=="" set "MODE=stack"

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%conda_run.ps1" -EnvName "%ENV_NAME%" -Mode "%MODE%"
