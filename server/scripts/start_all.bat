@echo off
setlocal
chcp 65001 >nul

set "SERVER_ROOT=%~dp0.."
for %%I in ("%SERVER_ROOT%") do set "SERVER_ROOT=%%~fI"
set "REPO_ROOT=%SERVER_ROOT%\.."
for %%I in ("%REPO_ROOT%") do set "REPO_ROOT=%%~fI"

set "ENV_NAME=%~1"
if not defined ENV_NAME set "ENV_NAME=sensor_server"
set "CONDA_EXE=%~2"
set "NGINX_START=%REPO_ROOT%\web\start.ps1"
set "NODE_WS_START=%REPO_ROOT%\websocket\start.ps1"

echo [start_all] starting server stack with Conda env %ENV_NAME%...
if defined CONDA_EXE (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SERVER_ROOT%\scripts\conda_run.ps1" -EnvName "%ENV_NAME%" -Mode "stack" -CondaExe "%CONDA_EXE%"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SERVER_ROOT%\scripts\conda_run.ps1" -EnvName "%ENV_NAME%" -Mode "stack"
)
if errorlevel 1 exit /b 1

if exist "%NODE_WS_START%" (
    echo [start_all] starting video websocket sidecar...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%NODE_WS_START%"
    if errorlevel 1 exit /b 1
) else (
    echo [start_all] websocket start script not found, skipping: %NODE_WS_START%
)

if exist "%NGINX_START%" (
    echo [start_all] starting web gateway...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%NGINX_START%"
    if errorlevel 1 exit /b 1
) else (
    echo [start_all] web start script not found, skipping: %NGINX_START%
)

echo [start_all] startup commands completed.
endlocal
