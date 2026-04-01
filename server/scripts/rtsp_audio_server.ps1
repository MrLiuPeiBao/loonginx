param(
    [string]$EnvName = "sensor_server",
    [string]$CondaExe = "",
    [string]$PythonExe = "",
    [string]$RtspHost = "127.0.0.1",
    [int]$Port = 8554,
    [string]$Mount = "/audio",
    [ValidateSet("sine", "mic", "file")]
    [string]$Source = "sine",
    [string]$File = "",
    [switch]$Loop,
    [int]$Freq = 1000,
    [int]$SampleRate = 16000,
    [int]$Channels = 1,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

if ($Help) {
    Write-Host 'Usage: powershell -ExecutionPolicy Bypass -File "server/scripts/rtsp_audio_server.ps1" -Source "sine"'
    return
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

$resolvedPython = ""
if ($PythonExe) {
    if (-not (Test-Path $PythonExe)) {
        Write-Error "Python executable not found: $PythonExe"
        exit 1
    }
    $resolvedPython = (Resolve-Path $PythonExe).Path
} else {
    $resolvedConda = Resolve-CondaCommand -PreferredPath $CondaExe
    if (-not $resolvedConda) {
        Write-Error "Failed to resolve conda. Please add it to PATH, pass -CondaExe, or pass -PythonExe."
        exit 1
    }
    $resolvedPython = Get-CondaPythonPath -Name $EnvName -CondaCommand $resolvedConda
}

$pythonPath = $resolvedPython
if (-not $pythonPath) {
    Write-Error "Failed to resolve python for env: $EnvName"
    exit 1
}
$pythonPath = $pythonPath -replace '\\', '/'
$envPrefix = Get-EnvPrefixFromPython -PythonPath $pythonPath
$envPathPrefix = (Get-CondaPathEntries -Prefix $envPrefix) -join ';'

$env:CONDA_PREFIX = "$envPrefix"
$env:PATH = "$envPathPrefix;$env:PATH"

$scriptPath = (Join-Path $PSScriptRoot "rtsp_audio_server.py") -replace '\\', '/'

$argsList = @(
    "--host", "$RtspHost",
    "--port", "$Port",
    "--mount", "$Mount",
    "--source", "$Source",
    "--freq", "$Freq",
    "--sample-rate", "$SampleRate",
    "--channels", "$Channels"
)
if ($File) { $argsList += @("--file", "$File") }
if ($Loop) { $argsList += "--loop" }

& "$pythonPath" "$scriptPath" @argsList
