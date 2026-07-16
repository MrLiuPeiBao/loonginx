param()

$ErrorActionPreference = 'Stop'
$webRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$nginx = Join-Path $webRoot 'nginx.exe'

if (-not (Test-Path $nginx)) {
    throw "nginx.exe not found: $nginx"
}

New-Item -ItemType Directory -Force -Path (Join-Path $webRoot 'logs') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $webRoot 'temp') | Out-Null

& $nginx -p "$webRoot\" -c 'conf\nginx.conf' -t
if ($LASTEXITCODE -ne 0) {
    throw 'Nginx configuration validation failed.'
}

$listener = Get-NetTCPConnection -LocalPort 8888 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    Write-Host "[web] port 8888 is already listening (PID=$($listener.OwningProcess))."
    return
}

$nginxPrefix = ($webRoot -replace '\\', '/').TrimEnd('/') + '/'
$nginxArguments = "-p `"$nginxPrefix`" -c conf/nginx.conf"
Start-Process -FilePath $nginx -ArgumentList $nginxArguments -WorkingDirectory $webRoot `
    -WindowStyle Hidden | Out-Null
Start-Sleep -Milliseconds 500
$listener = Get-NetTCPConnection -LocalPort 8888 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $listener) {
    throw 'Nginx did not start on port 8888. Check web/logs/error.log.'
}

Write-Host "[web] started on http://127.0.0.1:8888 (PID=$($listener.OwningProcess))."
