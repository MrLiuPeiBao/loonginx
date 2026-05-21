param(
    [string]$EnvName = "sensor_server",
    [string]$CondaExe = "",
    [string]$PythonExe = "",

    [string]$ApiBaseUrl = "http://127.0.0.1:8000/api",
    [string]$MqttBroker = "127.0.0.1",
    [int]$MqttPort = 1883,
    [string]$MqttUsername = "",
    [string]$MqttPassword = "",
    [string]$ClientId = "",
    [double]$DurationSeconds = 0,

    [string]$RtspHost = "127.0.0.1",
    [int]$RtspPort = 8554,
    [string]$RtspMount = "/audio",
    [ValidateSet("sine", "mic", "file")]
    [string]$RtspSource = "sine",
    [string]$RtspFile = "",
    [switch]$RtspLoop,
    [int]$RtspFreq = 1000,
    [int]$RtspSampleRate = 16000,
    [int]$RtspChannels = 1,

    [double]$SensorInterval = 1.0,
    [double]$BmsInterval = 2.0,
    [double]$RfidInterval = 3.0,
    [double]$CablewayInterval = 1.0,
    [double]$HelloInterval = 5.0,
    [double]$MediaInterval = 5.0,
    [double]$MetalInterval = 7.0,
    [string]$LogLevel = "INFO",

    [string]$MosquittoConf = "",
    [switch]$SkipMqttBroker,
    [switch]$NoSeedSensorConfigs,
    [switch]$NoHttpSeed,
    [int]$ClientStartDelaySeconds = 2,
    [int]$ApiReadyTimeoutSeconds = 30,
    [switch]$SkipApiReadyCheck,
    [string]$LogDir = "",
    [switch]$CloseOnExit,
    [switch]$DryRun,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Write-Host @"
Usage:
  powershell -ExecutionPolicy Bypass -File .\scripts\start_rtsp_virtual_client.ps1

Common examples:
  # Start Mosquitto if needed, RTSP audio simulator, and virtual client.
  powershell -ExecutionPolicy Bypass -File .\scripts\start_rtsp_virtual_client.ps1

  # Run the virtual client for 60 seconds.
  powershell -ExecutionPolicy Bypass -File .\scripts\start_rtsp_virtual_client.ps1 -DurationSeconds 60

  # Use an existing MQTT broker.
  powershell -ExecutionPolicy Bypass -File .\scripts\start_rtsp_virtual_client.ps1 -SkipMqttBroker -MqttBroker 192.168.1.20

  # Feed RTSP from a local audio file.
  powershell -ExecutionPolicy Bypass -File .\scripts\start_rtsp_virtual_client.ps1 -RtspSource file -RtspFile C:\data\sample.wav -RtspLoop

Parameters:
  -EnvName                 Conda environment name. Default: sensor_server
  -ApiBaseUrl              API base URL used by virtual_client.py. Default: http://127.0.0.1:8000/api
  -MqttBroker/-MqttPort    MQTT broker endpoint used by virtual_client.py.
  -SkipMqttBroker          Do not auto-start local Mosquitto.
  -SkipApiReadyCheck       Do not wait for /api/health/ready before launching the virtual client.
  -RtspHost/-RtspPort      RTSP bind endpoint. Default URL: rtsp://127.0.0.1:8554/audio
  -DurationSeconds         0 means run until manually stopped.
  -LogDir                  Defaults to logs\rtsp_virtual_client_<timestamp>.
  -CloseOnExit             Close child PowerShell windows when their process exits.
  -DryRun                  Print resolved commands without launching windows.
"@
    return
}

function Write-Step {
    param([string]$Message)
    Write-Host "[rtsp-client] $Message"
}

function Quote-PsString {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Quote-CmdArg {
    param([string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
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
        $candidates.Add((Join-Path $base "Scripts\conda.exe"))
        $candidates.Add((Join-Path $base "condabin\conda.bat"))
    }

    foreach ($candidate in $candidates) {
        if (-not $candidate) {
            continue
        }
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    return ""
}

function Get-CondaPythonPath {
    param(
        [string]$Name,
        [string]$CondaCommand
    )
    if (-not $CondaCommand) {
        return ""
    }
    try {
        $pythonPath = & $CondaCommand run -n "$Name" python -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -ne 0) {
            return ""
        }
        return ($pythonPath | Select-Object -First 1).Trim()
    } catch {
        return ""
    }
}

function Get-EnvPrefixFromPython {
    param([string]$PythonPath)
    if (-not $PythonPath) {
        return ""
    }
    return ((Split-Path -Parent $PythonPath) -replace "\\", "/")
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

function Get-MosquittoPath {
    param([string]$Prefix)
    if (-not $Prefix) {
        return ""
    }
    foreach ($candidate in @(
        "$Prefix/Library/sbin/mosquitto.exe",
        "$Prefix/Library/bin/mosquitto.exe",
        "$Prefix/Scripts/mosquitto.exe",
        "$Prefix/bin/mosquitto.exe",
        "$Prefix/mosquitto.exe"
    )) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return ""
}

function Test-TcpEndpointListening {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutMs = 500
    )
    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if (-not $connected) {
            return $false
        }
        $client.EndConnect($async)
        return $client.Connected
    } catch {
        return $false
    } finally {
        if ($client) {
            $client.Close()
        }
    }
}

function Get-ApiReadyUrl {
    param([string]$BaseUrl)

    $trimmed = [string]$BaseUrl
    $trimmed = $trimmed.Trim()
    if (-not $trimmed) {
        return ""
    }
    return ($trimmed.TrimEnd('/')) + "/health/ready"
}

function Wait-ApiReady {
    param(
        [string]$ReadyUrl,
        [int]$TimeoutSeconds = 30
    )

    if (-not $ReadyUrl) {
        return $false
    }

    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $ReadyUrl -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                return $true
            }
        } catch {
            try {
                $statusCode = [int]$_.Exception.Response.StatusCode
                if ($statusCode -eq 200) {
                    return $true
                }
            } catch {
            }
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Add-Arg {
    param(
        [System.Collections.Generic.List[string]]$ArgsList,
        [string]$Name,
        [object]$Value
    )
    $ArgsList.Add($Name)
    $ArgsList.Add((Quote-CmdArg ([string]$Value)))
}

function Build-NativeLoggedCommand {
    param(
        [string]$NativeCommand,
        [string]$LogPath,
        [string]$Prelude = ""
    )

    $logLiteral = Quote-PsString $LogPath
    $cmdLine = "$NativeCommand >> $(Quote-CmdArg $LogPath) 2>&1"
    $cmdLiteral = Quote-PsString $cmdLine
    $prefix = "Write-Host ('Logging to ' + $logLiteral); "
    if ($Prelude) {
        $prefix += "$Prelude; "
    }
    return $prefix + "cmd.exe /d /s /c $cmdLiteral; " +
        "`$exitCode = `$LASTEXITCODE; " +
        "if (`$null -ne `$exitCode -and `$exitCode -ne 0) { Write-Host ('Exited with code ' + `$exitCode + '; see log: ' + $logLiteral) }"
}

function Start-ToolWindow {
    param(
        [string]$Title,
        [string]$Command,
        [string]$WorkingDirectory,
        [string]$EnvPrefix,
        [string]$EnvPathPrefix,
        [bool]$KeepOpen
    )

    $titleLiteral = Quote-PsString $Title
    $dirLiteral = Quote-PsString $WorkingDirectory
    $prefixLiteral = Quote-PsString $EnvPrefix
    $pathLiteral = Quote-PsString $EnvPathPrefix

    $fullCommand = @(
        "`$host.UI.RawUI.WindowTitle = $titleLiteral",
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "[Console]::InputEncoding = [System.Text.Encoding]::UTF8",
        "`$OutputEncoding = [Console]::OutputEncoding",
        "chcp 65001 | Out-Null",
        "Set-Location $dirLiteral",
        "`$env:CONDA_PREFIX = $prefixLiteral",
        "`$env:PATH = $pathLiteral + ';' + `$env:PATH",
        $Command
    ) -join "; "

    $arguments = @("-ExecutionPolicy", "Bypass", "-Command", $fullCommand)
    if ($KeepOpen) {
        $arguments = @("-NoExit") + $arguments
    }

    Start-Process -FilePath "powershell" -WorkingDirectory $WorkingDirectory -ArgumentList $arguments | Out-Null
    Write-Step "Started $Title window."
}

$serverRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$rtspScript = (Resolve-Path (Join-Path $PSScriptRoot "rtsp_audio_server.ps1")).Path
$virtualClientScript = (Resolve-Path (Join-Path $PSScriptRoot "virtual_client.py")).Path

if ($PythonExe) {
    if (-not (Test-Path $PythonExe)) {
        Write-Error "Python executable not found: $PythonExe"
        exit 1
    }
    $pythonPath = (Resolve-Path $PythonExe).Path
} else {
    $resolvedConda = Resolve-CondaCommand -PreferredPath $CondaExe
    if (-not $resolvedConda) {
        Write-Error "Failed to resolve conda. Please add it to PATH, pass -CondaExe, or pass -PythonExe."
        exit 1
    }
    $pythonPath = Get-CondaPythonPath -Name $EnvName -CondaCommand $resolvedConda
}

if (-not $pythonPath) {
    Write-Error "Failed to resolve python for env: $EnvName"
    exit 1
}

$pythonPath = $pythonPath -replace "\\", "/"
$envPrefix = Get-EnvPrefixFromPython -PythonPath $pythonPath
$envPathPrefix = (Get-CondaPathEntries -Prefix $envPrefix) -join ";"
$env:CONDA_PREFIX = $envPrefix
$env:PATH = "$envPathPrefix;$env:PATH"

if (-not $ClientId) {
    $ClientId = "virtual_gateway_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}

if (-not $LogDir) {
    $LogDir = Join-Path $serverRoot ("logs\rtsp_virtual_client_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
}
$LogDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($LogDir)

$rtspUrl = "rtsp://$RtspHost`:$RtspPort$RtspMount"
$apiReadyUrl = Get-ApiReadyUrl -BaseUrl $ApiBaseUrl
$keepOpen = -not $CloseOnExit

$rtspPyScript = (Resolve-Path (Join-Path $PSScriptRoot "rtsp_audio_server.py")).Path
$rtspArgs = [System.Collections.Generic.List[string]]::new()
Add-Arg -ArgsList $rtspArgs -Name "--host" -Value $RtspHost
Add-Arg -ArgsList $rtspArgs -Name "--port" -Value $RtspPort
Add-Arg -ArgsList $rtspArgs -Name "--mount" -Value $RtspMount
Add-Arg -ArgsList $rtspArgs -Name "--source" -Value $RtspSource
Add-Arg -ArgsList $rtspArgs -Name "--freq" -Value $RtspFreq
Add-Arg -ArgsList $rtspArgs -Name "--sample-rate" -Value $RtspSampleRate
Add-Arg -ArgsList $rtspArgs -Name "--channels" -Value $RtspChannels
if ($RtspFile) {
    Add-Arg -ArgsList $rtspArgs -Name "--file" -Value $RtspFile
}
if ($RtspLoop) {
    $rtspArgs.Add("--loop")
}
$rtspLog = Join-Path $LogDir "rtsp_audio.log"
$rtspNativeCommand = "$(Quote-CmdArg $pythonPath) $(Quote-CmdArg $rtspPyScript) $($rtspArgs -join ' ')"
$rtspCommand = Build-NativeLoggedCommand -NativeCommand $rtspNativeCommand -LogPath $rtspLog

$clientArgs = [System.Collections.Generic.List[string]]::new()
Add-Arg -ArgsList $clientArgs -Name "--mqtt-broker" -Value $MqttBroker
Add-Arg -ArgsList $clientArgs -Name "--mqtt-port" -Value $MqttPort
if ($MqttUsername) {
    Add-Arg -ArgsList $clientArgs -Name "--mqtt-username" -Value $MqttUsername
}
if ($MqttPassword) {
    Add-Arg -ArgsList $clientArgs -Name "--mqtt-password" -Value $MqttPassword
}
Add-Arg -ArgsList $clientArgs -Name "--mqtt-client-id" -Value $ClientId
Add-Arg -ArgsList $clientArgs -Name "--api-base-url" -Value $ApiBaseUrl
Add-Arg -ArgsList $clientArgs -Name "--duration-seconds" -Value $DurationSeconds
Add-Arg -ArgsList $clientArgs -Name "--sensor-interval" -Value $SensorInterval
Add-Arg -ArgsList $clientArgs -Name "--bms-interval" -Value $BmsInterval
Add-Arg -ArgsList $clientArgs -Name "--rfid-interval" -Value $RfidInterval
Add-Arg -ArgsList $clientArgs -Name "--cableway-interval" -Value $CablewayInterval
Add-Arg -ArgsList $clientArgs -Name "--hello-interval" -Value $HelloInterval
Add-Arg -ArgsList $clientArgs -Name "--media-interval" -Value $MediaInterval
Add-Arg -ArgsList $clientArgs -Name "--metal-interval" -Value $MetalInterval
Add-Arg -ArgsList $clientArgs -Name "--log-level" -Value $LogLevel
if ($NoSeedSensorConfigs) {
    $clientArgs.Add("--no-seed-sensor-configs")
}
if ($NoHttpSeed) {
    $clientArgs.Add("--no-http-seed")
}
$clientLog = Join-Path $LogDir "virtual_client.log"
$clientNativeCommand = "$(Quote-CmdArg $pythonPath) $(Quote-CmdArg $virtualClientScript) $($clientArgs -join ' ')"
$clientCommand = Build-NativeLoggedCommand -NativeCommand $clientNativeCommand -LogPath $clientLog -Prelude "Start-Sleep -Seconds $ClientStartDelaySeconds"

$mosquittoCommand = ""
if (-not $SkipMqttBroker) {
    if (Test-TcpEndpointListening -HostName $MqttBroker -Port $MqttPort) {
        Write-Step "MQTT endpoint already listening: $MqttBroker`:$MqttPort"
    } else {
        $mosquittoPath = Get-MosquittoPath -Prefix $envPrefix
        if ($mosquittoPath) {
            if (-not $MosquittoConf) {
                $localConf = Join-Path $PSScriptRoot "mosquitto_lan.conf"
                $envConf = "$envPrefix/Library/etc/mosquitto/mosquitto.conf"
                if (Test-Path $localConf) {
                    $MosquittoConf = (Resolve-Path $localConf).Path
                } elseif (Test-Path $envConf) {
                    $MosquittoConf = $envConf
                }
            }
            $mosquittoLog = Join-Path $LogDir "mosquitto.log"
            if ($MosquittoConf) {
                $mosquittoNativeCommand = "$(Quote-CmdArg $mosquittoPath) -c $(Quote-CmdArg $MosquittoConf) -v"
            } else {
                $mosquittoNativeCommand = "$(Quote-CmdArg $mosquittoPath) -v"
            }
            $mosquittoCommand = Build-NativeLoggedCommand -NativeCommand $mosquittoNativeCommand -LogPath $mosquittoLog
        } else {
            Write-Warning "mosquitto not found in conda env. Virtual client will use existing broker at $MqttBroker`:$MqttPort."
        }
    }
}

Write-Step "Python: $pythonPath"
Write-Step "RTSP URL: $rtspUrl"
Write-Step "Virtual client id: $ClientId"
Write-Step "MQTT endpoint: $MqttBroker`:$MqttPort"
Write-Step "API base URL: $ApiBaseUrl"
if ($apiReadyUrl) {
    Write-Step "API ready URL: $apiReadyUrl"
}
Write-Step "Log directory: $LogDir"
Write-Step "If the API process should consume RTSP audio, start it with AUDIO_RTSP_INPUT=$rtspUrl."

if ($DryRun) {
    if ($mosquittoCommand) {
        Write-Host ""
        Write-Host "[mosquitto]"
        Write-Host $mosquittoCommand
    }
    Write-Host ""
    Write-Host "[rtsp]"
    Write-Host $rtspCommand
    Write-Host ""
    Write-Host "[virtual-client]"
    Write-Host $clientCommand
    return
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if ($mosquittoCommand) {
    Start-ToolWindow -Title "MQTT Broker" -Command $mosquittoCommand -WorkingDirectory $serverRoot -EnvPrefix $envPrefix -EnvPathPrefix $envPathPrefix -KeepOpen $keepOpen
    Start-Sleep -Seconds 1
}

Start-ToolWindow -Title "RTSP Audio Simulator" -Command $rtspCommand -WorkingDirectory $serverRoot -EnvPrefix $envPrefix -EnvPathPrefix $envPathPrefix -KeepOpen $keepOpen
if (-not $SkipApiReadyCheck) {
    Write-Step "Waiting for API readiness: $apiReadyUrl"
    if (-not (Wait-ApiReady -ReadyUrl $apiReadyUrl -TimeoutSeconds $ApiReadyTimeoutSeconds)) {
        Write-Error "API not ready at $apiReadyUrl. GUI reads /api/*, and MQTT data enters MySQL only after the API process subscribes to the broker. Start the server stack first."
        exit 1
    }
    Write-Step "API ready confirmed."
}
Start-ToolWindow -Title "Virtual Client Simulator" -Command $clientCommand -WorkingDirectory $serverRoot -EnvPrefix $envPrefix -EnvPathPrefix $envPathPrefix -KeepOpen $keepOpen

Write-Step "Started. Close the spawned PowerShell windows to stop RTSP/client processes."
