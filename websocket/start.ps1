$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$stdoutLog = Join-Path $projectRoot 'server.out.log'
$stderrLog = Join-Path $projectRoot 'server.err.log'
$nodeScript = Join-Path $projectRoot 'index.js'
$envFile = Join-Path $projectRoot '.env'

if (Test-Path $envFile) {
  foreach ($line in Get-Content $envFile -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) {
      continue
    }
    $name, $value = $trimmed.Split('=', 2)
    [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), 'Process')
  }
}

function Resolve-FFmpegPath {
  if ($env:FFMPEG_PATH -and (Test-Path $env:FFMPEG_PATH)) {
    return $env:FFMPEG_PATH
  }

  $command = Get-Command ffmpeg -ErrorAction SilentlyContinue
  if ($command) {
    return $command.Source
  }

  $winGetLink = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\ffmpeg.exe'
  if (Test-Path $winGetLink) {
    return $winGetLink
  }

  return $null
}

function Get-ProjectNodeProcesses {
  Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
    Where-Object {
      $_.CommandLine -match 'index\.js' -and $_.CommandLine -match [regex]::Escape($projectRoot)
    }
}

function Get-PortOwners {
  $camera1Port = if ($env:WS_PORT_CAMERA1) { [int]$env:WS_PORT_CAMERA1 } else { 8089 }
  $camera2Port = if ($env:WS_PORT_CAMERA2) { [int]$env:WS_PORT_CAMERA2 } else { 8090 }
  $mqttWsPort = if ($env:MQTT_WS_PORT) { [int]$env:MQTT_WS_PORT } else { 1884 }
  $ports = @($camera1Port, $camera2Port, $mqttWsPort)
  Get-NetTCPConnection -State Listen -LocalPort $ports -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
}

$ffmpegPath = Resolve-FFmpegPath
if (-not $ffmpegPath) {
  throw 'FFmpeg was not found. Install it first or set FFMPEG_PATH.'
}

$existingProcesses = Get-ProjectNodeProcesses
foreach ($process in $existingProcesses) {
  try {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    Write-Host "Stopped old process PID=$($process.ProcessId)"
  } catch {
    Write-Warning "Failed to stop PID=$($process.ProcessId): $($_.Exception.Message)"
  }
}

$portOwners = Get-PortOwners
foreach ($pid in $portOwners) {
  try {
    Stop-Process -Id $pid -Force -ErrorAction Stop
    Write-Host "Stopped port owner PID=$pid"
  } catch {
    Write-Warning "Failed to stop port owner PID=${pid}: $($_.Exception.Message)"
  }
}

if (Test-Path $stdoutLog) {
  Remove-Item $stdoutLog -Force
}

if (Test-Path $stderrLog) {
  Remove-Item $stderrLog -Force
}

$env:FFMPEG_PATH = $ffmpegPath
if (-not $env:RTSP_UNIFIED_BASE) {
  $env:RTSP_UNIFIED_BASE = 'rtsp://127.0.0.1:8554'
}
if (-not $env:RTSP_URL_CAMERA1) {
  $env:RTSP_URL_CAMERA1 = "$($env:RTSP_UNIFIED_BASE.TrimEnd('/'))/cam01"
}
if (-not $env:RTSP_URL_CAMERA2) {
  $env:RTSP_URL_CAMERA2 = "$($env:RTSP_UNIFIED_BASE.TrimEnd('/'))/cam02"
}
$process = Start-Process -FilePath node `
  -ArgumentList $nodeScript `
  -WorkingDirectory $projectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog `
  -PassThru

Start-Sleep -Seconds 2

if ($process.HasExited) {
  Write-Host 'Startup failed. Error log:'
  if (Test-Path $stderrLog) {
    Get-Content $stderrLog
  }
  exit 1
}

Write-Host "Started successfully. PID=$($process.Id)"
Write-Host "FFmpeg: $ffmpegPath"
Write-Host "Stdout log: $stdoutLog"
Write-Host "Stderr log: $stderrLog"
