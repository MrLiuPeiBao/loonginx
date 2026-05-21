@echo off
setlocal
chcp 65001 >nul

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "CONDA_BAT=C:\Users\lpb\anaconda3\condabin\conda.bat"
set "NGINX_ROOT=C:\Users\lpb\Desktop\coil-design-v2.1.1"
set "NGINX_EXE=%NGINX_ROOT%\nginx.exe"
set "NODE_WS_ROOT=C:\Users\lpb\Desktop\nodejs-ws"
set "NODE_WS_START=%NODE_WS_ROOT%\start.ps1"

if not exist "%CONDA_BAT%" (
    echo [start_all] conda.bat not found: %CONDA_BAT%
    pause
    exit /b 1
)

echo [start_all] starting server stack...
call "%CONDA_BAT%" run -n sensor_server powershell -ExecutionPolicy Bypass -File "%ROOT%\scripts\conda_run.ps1" -EnvName "sensor_server" -Mode "stack"
if errorlevel 1 (
    echo [start_all] failed to start stack
    pause
    exit /b 1
)

if exist "%NGINX_EXE%" (
    echo [start_all] starting nginx 8888...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$p = Get-NetTCPConnection -LocalPort 8888 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; if ($p) { Write-Host '[start_all] nginx already listening on 8888 pid=' $p } else { Start-Process -FilePath '%NGINX_EXE%' -WorkingDirectory '%NGINX_ROOT%' -ArgumentList '-p','.\','-c','conf\nginx.conf' -WindowStyle Hidden | Out-Null; Write-Host '[start_all] nginx started' }"
) else (
    echo [start_all] nginx.exe not found, skip nginx startup: %NGINX_EXE%
)

if exist "%NODE_WS_START%" (
    echo [start_all] starting nodejs-ws...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%NODE_WS_START%"
    if errorlevel 1 (
        echo [start_all] nodejs-ws startup failed
        pause
        exit /b 1
    )
) else (
    echo [start_all] nodejs-ws start script not found, skip: %NODE_WS_START%
)

echo [start_all] done.
pause
endlocal
