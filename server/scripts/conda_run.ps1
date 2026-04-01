param(
    [ValidateSet('stack', 'api', 'gui', 'mqtt')]
    [string]$Mode = 'stack',
    [string]$EnvName = 'sensor_server',
    [string]$MySqlServiceName = 'MySQL80',
    [string]$CondaExe = '',
    [switch]$SkipLocalRtspAudio,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

if ($Help) {
    Write-Host @"
Usage:
  powershell -ExecutionPolicy Bypass -File conda_run.ps1 -EnvName "sensor_server" -Mode "stack"

Parameters:
  -EnvName            Conda environment name (default: sensor_server)
  -Mode               stack | api | gui | mqtt
  -MySqlServiceName   Windows service name for MySQL (default: MySQL80)
  -CondaExe           Optional path to conda.exe/conda.bat
  -SkipLocalRtspAudio Do not auto-start the local RTSP audio simulator
"@
    return
}

function Write-Step {
    param([string]$Message)
    Write-Host "[conda_run] $Message"
}

function Resolve-CondaCommand {
    param([string]$PreferredPath)

    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($PreferredPath) {
        $candidates.Add($PreferredPath)
    }
    if ($env:CONDA_EXE) {
        $candidates.Add($env:CONDA_EXE)
    }

    $command = Get-Command "conda" -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        $candidates.Add($command.Source)
    }

    foreach ($base in @(
        "$env:USERPROFILE\anaconda3",
        "$env:USERPROFILE\Anaconda3",
        "$env:USERPROFILE\miniconda3",
        "$env:USERPROFILE\Miniconda3"
    )) {
        $candidates.Add((Join-Path $base 'Scripts\conda.exe'))
        $candidates.Add((Join-Path $base 'condabin\conda.bat'))
    }

    foreach ($candidate in $candidates) {
        if (-not $candidate) {
            continue
        }
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    return ''
}

function Get-EnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$DefaultValue
    )
    if (-not (Test-Path $Path)) {
        return $DefaultValue
    }
    $target = $Key.Trim().ToUpperInvariant()
    foreach ($line in Get-Content -Path $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }
        $idx = $trimmed.IndexOf('=')
        if ($idx -lt 1) {
            continue
        }
        $key = $trimmed.Substring(0, $idx).Trim().ToUpperInvariant()
        if ($key -ne $target) {
            continue
        }
        $value = $trimmed.Substring($idx + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            if ($value.Length -ge 2) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        if ($value -ne '') {
            return $value
        }
        return $DefaultValue
    }
    return $DefaultValue
}

function Test-CondaAvailable {
    return [bool]$script:CondaCommand
}

function Test-CondaEnv {
    param([string]$Name)
    return [bool](Get-CondaPythonPath -Name $Name)
}

function Get-CondaPythonPath {
    param([string]$Name)
    if (-not $script:CondaCommand) {
        return ''
    }
    try {
        $pythonPath = & $script:CondaCommand run -n "$Name" python -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -ne 0) {
            return ''
        }
        return ($pythonPath | Select-Object -First 1).Trim()
    } catch {
        return ''
    }
}

function Get-EnvPrefixFromPython {
    param([string]$PythonPath)
    if (-not $PythonPath) {
        return ''
    }
    $prefix = Split-Path -Parent $PythonPath
    return ($prefix -replace '\\', '/')
}

function Get-CondaPathEntries {
    param([string]$Prefix)
    if (-not $Prefix) {
        return @()
    }
    return @(
        "$Prefix",
        "$Prefix/Library/bin",
        "$Prefix/Library/sbin",
        "$Prefix/Scripts",
        "$Prefix/bin"
    )
}

function Test-EnvExecutable {
    param(
        [string]$Prefix,
        [string[]]$Names
    )
    foreach ($name in $Names) {
        $candidates = @(
            "$Prefix/$name",
            "$Prefix/Library/bin/$name",
            "$Prefix/Library/sbin/$name",
            "$Prefix/Scripts/$name",
            "$Prefix/bin/$name"
        )
        foreach ($candidate in $candidates) {
            if (Test-Path $candidate) {
                return $true
            }
        }
    }
    return $false
}

function Test-ExecutableOnPath {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-MosquittoPath {
    param([string]$Prefix)
    if (-not $Prefix) {
        return ''
    }
    $candidates = @(
        "$Prefix/Library/sbin/mosquitto.exe",
        "$Prefix/Library/bin/mosquitto.exe",
        "$Prefix/Scripts/mosquitto.exe",
        "$Prefix/bin/mosquitto.exe",
        "$Prefix/mosquitto.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return ''
}

function Start-MySqlService {
    param([string]$PreferredName)

    $service = Get-Service -Name "$PreferredName" -ErrorAction SilentlyContinue
    if (-not $service) {
        $service = Get-Service | Where-Object {
            $_.Name -match 'mysql' -or $_.DisplayName -match 'mysql'
        } | Select-Object -First 1
    }
    if (-not $service) {
        Write-Warning "MySQL service not found. Please start it manually."
        return
    }
    if ($service.Status -eq 'Running') {
        Write-Step "MySQL service already running: $($service.Name)"
        return
    }
    try {
        Start-Service -Name "$($service.Name)"
        Write-Step "MySQL service started: $($service.Name)"
    } catch {
        Write-Warning "Failed to start MySQL service ($($service.Name)). Please start it manually."
    }
}

function Start-ServerWindow {
    param(
        [string]$Title,
        [string]$Command,
        [string]$WorkingDirectory,
        [string]$EnvPrefix,
        [string]$EnvPathPrefix
    )
    $safeDir = $WorkingDirectory.Replace('"', '""')
    $titleCmd = "`$host.UI.RawUI.WindowTitle = '$Title';"
    $encodingCmd = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; [Console]::InputEncoding = [System.Text.Encoding]::UTF8; `$OutputEncoding = [Console]::OutputEncoding; chcp 65001 | Out-Null;"
    $cdCmd = "Set-Location `"$safeDir`";"
    $envCmd = ""
    if ($EnvPrefix) {
        $escapedPrefix = $EnvPrefix.Replace("'", "''")
        $envCmd = "$envCmd `$env:CONDA_PREFIX = '$escapedPrefix';"
    }
    if ($EnvPathPrefix) {
        $escapedPath = $EnvPathPrefix.Replace("'", "''")
        $envCmd = "$envCmd `$env:PATH = '$escapedPath;' + `$env:PATH;"
    }
    $fullCmd = "$titleCmd $encodingCmd $cdCmd $envCmd $Command"
    Start-Process -FilePath "powershell" -WorkingDirectory "$safeDir" -ArgumentList "-NoExit", "-Command", $fullCmd | Out-Null
    Write-Step "Started $Title window."
}

function Get-BoolEnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [bool]$DefaultValue = $false
    )

    $defaultText = if ($DefaultValue) { 'true' } else { 'false' }
    $value = (Get-EnvValue -Path $Path -Key $Key -DefaultValue $defaultText).Trim().ToLowerInvariant()
    switch ($value) {
        '1' { return $true }
        'true' { return $true }
        'yes' { return $true }
        'on' { return $true }
        '0' { return $false }
        'false' { return $false }
        'no' { return $false }
        'off' { return $false }
        default { return $DefaultValue }
    }
}

function Test-LoopbackHost {
    param([string]$HostName)
    if (-not $HostName) {
        return $false
    }
    return @('127.0.0.1', 'localhost', '::1') -contains $HostName.Trim().ToLowerInvariant()
}

function Get-LocalRtspEndpoint {
    param([string]$Url)
    if (-not $Url) {
        return $null
    }
    try {
        $uri = [System.Uri]$Url
    } catch {
        return $null
    }
    if ($uri.Scheme -ne 'rtsp') {
        return $null
    }
    if (-not (Test-LoopbackHost -HostName $uri.Host)) {
        return $null
    }
    $port = if ($uri.IsDefaultPort) { 554 } else { $uri.Port }
    $mount = if ($uri.AbsolutePath) { $uri.AbsolutePath } else { '/' }
    return [pscustomobject]@{
        Host = $uri.Host
        Port = $port
        Mount = $mount
    }
}

function Test-TcpEndpointListening {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutMs = 800
    )

    $client = New-Object System.Net.Sockets.TcpClient
    $async = $null
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        $client.EndConnect($async) | Out-Null
        return $true
    } catch {
        return $false
    } finally {
        if ($async -and $async.AsyncWaitHandle) {
            $async.AsyncWaitHandle.Close()
        }
        $client.Close()
    }
}

$serverRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$serverRootPath = $serverRoot.Path -replace '\\', '/'
$envPath = Join-Path $serverRoot.Path ".env"

Write-Step "Server root: $serverRootPath"

$script:CondaCommand = Resolve-CondaCommand -PreferredPath $CondaExe

if (-not (Test-CondaAvailable)) {
    Write-Error "conda not found. Please add it to PATH or pass -CondaExe."
    exit 1
}

if (-not (Test-CondaEnv -Name $EnvName)) {
    Write-Error "Conda env not found or broken: $EnvName"
    exit 1
}

$pythonPath = Get-CondaPythonPath -Name $EnvName
if (-not $pythonPath) {
    Write-Error "Failed to resolve conda python for env: $EnvName"
    exit 1
}
$pythonPath = $pythonPath -replace '\\', '/'
$envPrefix = Get-EnvPrefixFromPython -PythonPath $pythonPath
$envPathPrefix = (Get-CondaPathEntries -Prefix $envPrefix) -join ';'

$apiHost = Get-EnvValue -Path $envPath -Key "API_HOST" -DefaultValue "0.0.0.0"
$apiPort = Get-EnvValue -Path $envPath -Key "API_PORT" -DefaultValue "8000"
$audioEnabled = Get-BoolEnvValue -Path $envPath -Key "AUDIO_ENABLED" -DefaultValue $false
$audioRtspInput = Get-EnvValue -Path $envPath -Key "AUDIO_RTSP_INPUT" -DefaultValue ""
$audioRtspSource = Get-EnvValue -Path $envPath -Key "AUDIO_RTSP_SIM_SOURCE" -DefaultValue "sine"
$audioRtspFile = Get-EnvValue -Path $envPath -Key "AUDIO_RTSP_SIM_FILE" -DefaultValue ""
$audioRtspLoop = Get-BoolEnvValue -Path $envPath -Key "AUDIO_RTSP_SIM_LOOP" -DefaultValue $false
$audioRtspFreq = Get-EnvValue -Path $envPath -Key "AUDIO_RTSP_SIM_FREQ" -DefaultValue "1000"
$audioRtspSampleRate = Get-EnvValue -Path $envPath -Key "AUDIO_RTSP_SIM_SAMPLE_RATE" -DefaultValue "16000"
$audioRtspChannels = Get-EnvValue -Path $envPath -Key "AUDIO_RTSP_SIM_CHANNELS" -DefaultValue "1"

Write-Step "API host: $apiHost"
Write-Step "API port: $apiPort"

if (Test-EnvExecutable -Prefix $envPrefix -Names @('ffmpeg.exe')) {
    Write-Step "FFmpeg found in conda env."
} else {
    Write-Warning "FFmpeg not found in conda env (ffmpeg)."
}

if (Test-EnvExecutable -Prefix $envPrefix -Names @('ffplay.exe')) {
    Write-Step "FFplay found in conda env."
} elseif (Test-ExecutableOnPath -Name "ffplay") {
    Write-Step "FFplay found in system PATH."
} else {
    Write-Warning "FFplay not found (ffplay)."
}

if (Test-EnvExecutable -Prefix $envPrefix -Names @('gst-launch-1.0.exe')) {
    Write-Step "GStreamer found in conda env."
} else {
    Write-Warning "GStreamer not found in conda env (gst-launch-1.0)."
}

$startMqtt = $Mode -in @('stack', 'mqtt')
$startApi = $Mode -in @('stack', 'api')
$startGui = $Mode -in @('stack', 'gui')
$needMySql = $Mode -in @('stack', 'api', 'gui')

if ($needMySql) {
    Start-MySqlService -PreferredName $MySqlServiceName
}

if ($startMqtt) {
    $mosquittoPath = Get-MosquittoPath -Prefix $envPrefix
    if ($mosquittoPath) {
        $mosquittoConf = "$envPrefix/Library/etc/mosquitto/mosquitto.conf"
        if (Test-Path $mosquittoConf) {
            $mqttCommand = "`"$mosquittoPath`" -c `"$mosquittoConf`" -v"
        } else {
            Write-Warning "mosquitto.conf not found at $mosquittoConf, starting without config."
            $mqttCommand = "`"$mosquittoPath`" -v"
        }
        Start-ServerWindow -Title "MQTT" -Command $mqttCommand -WorkingDirectory $serverRootPath -EnvPrefix $envPrefix -EnvPathPrefix $envPathPrefix
    } else {
        Write-Warning "mosquitto not found in conda env. MQTT broker not started."
    }
}

$shouldEnsureLocalRtspAudio = (-not $SkipLocalRtspAudio) -and ($startApi -or $startGui) -and $audioEnabled
if ($shouldEnsureLocalRtspAudio) {
    $localRtspEndpoint = Get-LocalRtspEndpoint -Url $audioRtspInput
    if ($localRtspEndpoint) {
        if (Test-TcpEndpointListening -HostName $localRtspEndpoint.Host -Port $localRtspEndpoint.Port) {
            Write-Step "Local RTSP audio endpoint already listening: $audioRtspInput"
        } else {
            $rtspScriptPath = (Join-Path $PSScriptRoot 'rtsp_audio_server.ps1') -replace '\\', '/'
            if (Test-Path $rtspScriptPath) {
                $rtspCommand = "& `"$rtspScriptPath`" -EnvName `"$EnvName`" -PythonExe `"$pythonPath`" -RtspHost `"$($localRtspEndpoint.Host)`" -Port $($localRtspEndpoint.Port) -Mount `"$($localRtspEndpoint.Mount)`" -Source `"$audioRtspSource`" -Freq $audioRtspFreq -SampleRate $audioRtspSampleRate -Channels $audioRtspChannels"
                if ($audioRtspFile) {
                    $safeFile = ($audioRtspFile -replace '"', '""')
                    $rtspCommand += " -File `"$safeFile`""
                }
                if ($audioRtspLoop) {
                    $rtspCommand += " -Loop"
                }
                Start-ServerWindow -Title "RTSP Audio" -Command $rtspCommand -WorkingDirectory $serverRootPath -EnvPrefix $envPrefix -EnvPathPrefix $envPathPrefix
                Start-Sleep -Seconds 2
            } else {
                Write-Warning "Local RTSP audio script not found: $rtspScriptPath"
            }
        }
    }
}

if ($startApi) {
    $apiCommand = "`"$pythonPath`" -m uvicorn main:app --host `"$apiHost`" --port `"$apiPort`""
    Start-ServerWindow -Title "API" -Command $apiCommand -WorkingDirectory $serverRootPath -EnvPrefix $envPrefix -EnvPathPrefix $envPathPrefix
}

if ($startGui) {
    $guiCommand = "`"$pythonPath`" -m app.gui.app"
    Start-ServerWindow -Title "GUI" -Command $guiCommand -WorkingDirectory $serverRootPath -EnvPrefix $envPrefix -EnvPathPrefix $envPathPrefix
}

Write-Step "Done."
