param()

$ErrorActionPreference = 'Stop'
$webRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$nginx = Join-Path $webRoot 'nginx.exe'

if (-not (Test-Path $nginx)) {
    throw "nginx.exe not found: $nginx"
}

& $nginx -p "$webRoot\" -c 'conf\nginx.conf' -s quit
if ($LASTEXITCODE -ne 0) {
    throw 'Nginx graceful stop failed. Check web/logs/error.log.'
}

Write-Host '[web] stop signal sent.'
