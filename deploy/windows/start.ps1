[CmdletBinding()]
param(
    [string]$EnvName = 'sensor_server',
    [string]$CondaExe = '',
    [switch]$SkipWebsocket
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$serverRunner = Join-Path $repositoryRoot 'server\scripts\conda_run.ps1'
$websocketRunner = Join-Path $repositoryRoot 'websocket\start.ps1'
$webRunner = Join-Path $repositoryRoot 'web\start.ps1'

$serverArguments = @('-EnvName', $EnvName, '-Mode', 'stack')
if ($CondaExe) {
    $serverArguments += @('-CondaExe', $CondaExe)
}

& $serverRunner @serverArguments
if (-not $SkipWebsocket) {
    & $websocketRunner
}
& $webRunner

Write-Host '[start] startup commands completed. Run verify.ps1 after services settle.'
