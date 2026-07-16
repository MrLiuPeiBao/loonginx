[CmdletBinding()]
param(
    [string]$CondaExe = '',
    [string]$EnvName = 'sensor_server',
    [string]$ServerIp = '127.0.0.1',
    [string]$MySqlRootPassword = '',
    [string]$MySqlAppPassword = '',
    [string]$Camera1Rtsp = '',
    [string]$Camera2Rtsp = '',
    [switch]$SkipServerDependencies,
    [switch]$SkipMySqlInstall,
    [switch]$SkipNodeInstall,
    [switch]$ConfigureFirewall
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$serverRoot = Join-Path $repositoryRoot 'server'
$webRoot = Join-Path $repositoryRoot 'web'
$websocketRoot = Join-Path $repositoryRoot 'websocket'
$serverEnv = Join-Path $serverRoot '.env'
$websocketEnv = Join-Path $websocketRoot '.env'

function Set-EnvValue {
    param(
        [string]$Path,
        [string]$Name,
        [string]$Value
    )

    $lines = if (Test-Path $Path) { @(Get-Content $Path -Encoding UTF8) } else { @() }
    $pattern = '^' + [regex]::Escape($Name) + '='
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match $pattern) {
            $found = $true
            "$Name=$Value"
        } else {
            $line
        }
    }
    if (-not $found) {
        $updated += "$Name=$Value"
    }
    [IO.File]::WriteAllLines(
        $Path,
        [string[]]$updated,
        [Text.UTF8Encoding]::new($false)
    )
}

function Get-EnvValue {
    param([string]$Path, [string]$Name)

    if (-not (Test-Path $Path)) {
        return ''
    }
    $pattern = '^' + [regex]::Escape($Name) + '=(.*)$'
    foreach ($line in Get-Content $Path -Encoding UTF8) {
        if ($line -match $pattern) {
            return $Matches[1].Trim()
        }
    }
    return ''
}

function Read-PlainSecret {
    param([string]$Prompt)

    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

foreach ($required in @($serverRoot, $webRoot, $websocketRoot)) {
    if (-not (Test-Path $required)) {
        throw "Required deployment directory not found: $required"
    }
}

if (-not (Test-Path $serverEnv)) {
    Copy-Item (Join-Path $serverRoot '.env.example') $serverEnv
    Write-Host '[install] created server/.env from the safe template.'
}
if (-not (Test-Path $websocketEnv)) {
    Copy-Item (Join-Path $websocketRoot '.env.example') $websocketEnv
    Write-Host '[install] created websocket/.env from the safe template.'
}

$serverOrigin = "http://${ServerIp}:8888"
$websocketOrigin = "ws://${ServerIp}:8888"
Set-EnvValue $serverEnv 'API_BASE_URL' $serverOrigin
Set-EnvValue $serverEnv 'CORS_ALLOW_ORIGINS' "$serverOrigin,http://127.0.0.1:8888,http://localhost:8888"
Set-EnvValue $serverEnv 'YOLO_VIDEO_URL' "$websocketOrigin/ws/camera1"
Set-EnvValue $serverEnv 'AUDIO_VIDEO_URL' "$websocketOrigin/ws/camera2"
Set-EnvValue $serverEnv 'MQTT_BROKER_URL' "$websocketOrigin/mqtt"

$existingAppPassword = Get-EnvValue $serverEnv 'MYSQL_PASSWORD'
if (-not $MySqlAppPassword -and -not $existingAppPassword) {
    $MySqlAppPassword = Read-PlainSecret 'MySQL application user password'
}
if ($MySqlAppPassword) {
    Set-EnvValue $serverEnv 'MYSQL_PASSWORD' $MySqlAppPassword
}

if ($Camera1Rtsp -or $Camera2Rtsp) {
    if (-not $Camera1Rtsp -or -not $Camera2Rtsp) {
        throw 'Camera1Rtsp and Camera2Rtsp must be provided together.'
    }
    $localConfigDir = Join-Path $serverRoot 'config\local'
    New-Item -ItemType Directory -Force -Path $localConfigDir | Out-Null
    $go2rtcConfig = @(
        'api:',
        '  listen: ":1984"',
        'rtsp:',
        '  listen: ":8554"',
        'streams:',
        '  cam01:',
        "    - `"$Camera1Rtsp`"",
        '  cam02:',
        "    - `"$Camera2Rtsp`""
    )
    [IO.File]::WriteAllLines(
        (Join-Path $localConfigDir 'go2rtc.yaml'),
        [string[]]$go2rtcConfig,
        [Text.UTF8Encoding]::new($false)
    )
    Set-EnvValue $serverEnv 'MEDIA_GATEWAY_ENABLED' 'true'
    Write-Host '[install] created ignored local go2rtc configuration.'
}

if (-not $SkipServerDependencies) {
    if (-not $SkipMySqlInstall -and -not $MySqlRootPassword) {
        $MySqlRootPassword = Read-PlainSecret 'Existing or desired local MySQL root password'
    }
    $installer = Join-Path $serverRoot 'scripts\install_from_anaconda.ps1'
    $installerArguments = @{ EnvName = $EnvName }
    if ($CondaExe) {
        $installerArguments.CondaExe = $CondaExe
    }
    if ($MySqlRootPassword) {
        $installerArguments.MySqlRootPassword = $MySqlRootPassword
    }
    if ($SkipMySqlInstall) {
        $installerArguments.SkipMySqlInstall = $true
    }
    & $installer @installerArguments
}

if (-not $SkipNodeInstall) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw 'npm.cmd was not found. Install Node.js 18 or newer, then rerun this script.'
    }
    Push-Location $websocketRoot
    try {
        & $npm.Source ci --omit=dev
        if ($LASTEXITCODE -ne 0) {
            throw 'npm ci failed.'
        }
    } finally {
        Pop-Location
    }
}

$nginx = Join-Path $webRoot 'nginx.exe'
New-Item -ItemType Directory -Force -Path (Join-Path $webRoot 'logs') | Out-Null
& $nginx -p "$webRoot\" -c 'conf\nginx.conf' -t
if ($LASTEXITCODE -ne 0) {
    throw 'Nginx configuration validation failed.'
}

if ($ConfigureFirewall) {
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )) {
        throw 'ConfigureFirewall requires an elevated PowerShell session.'
    }
    foreach ($rule in @(
        @{ Name = 'Loonginx-Web-8888'; Port = 8888 },
        @{ Name = 'Loonginx-MQTT-1883'; Port = 1883 }
    )) {
        if (-not (Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Action Allow `
                -Protocol TCP -LocalPort $rule.Port -Profile Private | Out-Null
        }
    }
}

Write-Host ''
Write-Host '[install] deployment files are ready.'
Write-Host "[install] review local secrets in: $serverEnv"
Write-Host "[install] start with: deploy\windows\start.ps1 -EnvName $EnvName"
Write-Host "[install] browser URL: $serverOrigin/index"
