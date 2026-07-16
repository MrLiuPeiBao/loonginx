[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:8888',
    [switch]$RequireMediaSidecar
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$webRoot = Join-Path $repositoryRoot 'web'
$nginx = Join-Path $webRoot 'nginx.exe'

& $nginx -p "$webRoot\" -c 'conf\nginx.conf' -t
if ($LASTEXITCODE -ne 0) {
    throw 'Nginx configuration validation failed.'
}

$node = Get-Command node.exe -ErrorAction SilentlyContinue
if ($node) {
    & $node.Source --check (Join-Path $repositoryRoot 'websocket\index.js')
    if ($LASTEXITCODE -ne 0) {
        throw 'websocket/index.js syntax validation failed.'
    }
}

$checks = @(
    @{ Name = 'web'; Url = "$BaseUrl/index" },
    @{ Name = 'api-live'; Url = "$BaseUrl/api/health/live" },
    @{ Name = 'api-ready'; Url = "$BaseUrl/api/health/ready" },
    @{ Name = 'runtime-config'; Url = "$BaseUrl/api/runtime-config" }
)
foreach ($check in $checks) {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $check.Url -TimeoutSec 5
    if ($response.StatusCode -ne 200) {
        throw "$($check.Name) returned HTTP $($response.StatusCode)."
    }
    Write-Host "[verify] $($check.Name): HTTP $($response.StatusCode)"
}

$requiredPorts = @(8888, 8000, 1883)
if ($RequireMediaSidecar) {
    $requiredPorts += @(1884, 8089, 8090)
}
foreach ($port in $requiredPorts) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $listener) {
        throw "Required local port $port is not listening."
    }
    Write-Host "[verify] port ${port}: PID=$($listener.OwningProcess)"
}

Write-Host '[verify] deployment checks passed.'
