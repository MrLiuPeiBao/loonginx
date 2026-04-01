param(
    [string]$EnvName = 'sensor_server',
    [string]$PythonVersion = '3.11',
    [string]$CondaExe = '',
    [string]$MySqlServiceName = '',
    [string]$MySqlRootPassword = '',
    [string]$MySqlWingetId = 'Oracle.MySQL',
    [ValidateSet('Developer', 'Server', 'Dedicated', 'Manual')]
    [string]$MySqlConfigType = 'Server',
    [switch]$SkipMySqlInstall,
    [switch]$SkipCondaPackages,
    [switch]$SkipPipInstall,
    [switch]$SkipDatabaseCheck,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

if ($Help) {
    Write-Host @"
Usage:
  powershell -ExecutionPolicy Bypass -File install_from_anaconda.ps1

Parameters:
  -EnvName            Conda environment name (default: sensor_server)
  -PythonVersion      Python version for new env (default: 3.11)
  -CondaExe           Optional full path to conda.exe
  -MySqlServiceName   Preferred local MySQL Windows service name
  -MySqlRootPassword  Root password used when configuring local MySQL
  -MySqlWingetId      winget package id for MySQL server (default: Oracle.MySQL)
  -MySqlConfigType    Developer | Server | Dedicated | Manual (default: Server)
  -SkipMySqlInstall   Do not auto-install/configure local MySQL
  -SkipCondaPackages  Skip conda package installation
  -SkipPipInstall     Skip pip install -r requirements-lock.txt
  -SkipDatabaseCheck  Skip MySQL connectivity/database creation check

Notes:
  - Local MySQL installation/configuration requires an elevated PowerShell session.
  - If .env uses MYSQL_HOST=localhost/127.0.0.1 and MySQL is missing, the script will
    try to install and configure MySQL automatically unless -SkipMySqlInstall is set.
"@
    return
}

function Write-Step {
    param([string]$Message)
    Write-Host "[install] $Message"
}

function Resolve-CondaCommand {
    param([string]$PreferredPath)

    $candidates = New-Object System.Collections.Generic.List[string]
    if ($PreferredPath) {
        $candidates.Add($PreferredPath)
    }
    if ($env:CONDA_EXE) {
        $candidates.Add($env:CONDA_EXE)
    }

    foreach ($name in @('conda.exe', 'conda')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) {
            $candidates.Add($cmd.Source)
        }
    }

    $roots = New-Object System.Collections.Generic.List[string]
    foreach ($root in @(
        "$env:USERPROFILE\anaconda3",
        "$env:USERPROFILE\Anaconda3",
        "$env:LOCALAPPDATA\anaconda3",
        "$env:LOCALAPPDATA\Anaconda3",
        "C:\ProgramData\anaconda3",
        "C:\ProgramData\Anaconda3"
    )) {
        if ($root) {
            $roots.Add($root)
        }
    }

    foreach ($drive in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
        foreach ($leaf in @('anaconda', 'Anaconda', 'anaconda3', 'Anaconda3', 'miniconda3', 'Miniconda3')) {
            $roots.Add((Join-Path $drive.Root $leaf))
        }
    }

    foreach ($root in ($roots | Select-Object -Unique)) {
        $candidates.Add((Join-Path $root 'Scripts\conda.exe'))
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ($candidate -and (Test-Path $candidate)) {
            return (Resolve-Path $candidate).Path
        }
    }

    return ''
}

function Invoke-Conda {
    param([string[]]$Arguments)

    & $script:CondaCommand @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "conda command failed: $($Arguments -join ' ')"
    }
}

function Resolve-WingetCommand {
    foreach ($name in @('winget.exe', 'winget')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) {
            return $cmd.Source
        }
    }
    return ''
}

function Invoke-Winget {
    param([string[]]$Arguments)

    if (-not $script:WingetCommand) {
        throw 'winget not found. Install App Installer or set up winget first.'
    }
    & $script:WingetCommand @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "winget command failed: $($Arguments -join ' ')"
    }
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ExistingMySqlService {
    param([string]$PreferredName)

    if ($PreferredName) {
        $preferred = Get-CimInstance Win32_Service -Filter "Name='$PreferredName'" -ErrorAction SilentlyContinue
        if ($preferred) {
            return $preferred
        }
    }

    return Get-CimInstance Win32_Service -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '^MySQL' -or
            $_.DisplayName -match 'MySQL' -or
            $_.PathName -match 'mysqld\.exe'
        } |
        Sort-Object Name |
        Select-Object -First 1
}

function Resolve-MySqlBinary {
    param(
        [string]$BinaryName,
        [string]$PreferredServiceName = ''
    )

    $service = Get-ExistingMySqlService -PreferredName $PreferredServiceName
    if ($service -and $service.PathName) {
        $serviceMatch = [regex]::Match($service.PathName, '(?i)([A-Z]:\\[^"]*\\bin\\mysqld\.exe)')
        if ($serviceMatch.Success) {
            $binDir = Split-Path -Parent $serviceMatch.Groups[1].Value
            $candidate = Join-Path $binDir $BinaryName
            if (Test-Path $candidate) {
                return $candidate
            }
        }
    }

    $command = Get-Command $BinaryName -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return $command.Source
    }

    $roots = @(
        'C:\Program Files\MySQL',
        'C:\Program Files (x86)\MySQL'
    )

    foreach ($root in $roots) {
        if (-not (Test-Path $root)) {
            continue
        }
        foreach ($dir in (Get-ChildItem $root -Directory -Filter 'MySQL Server *' -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
            $candidate = Join-Path $dir.FullName "bin\$BinaryName"
            if (Test-Path $candidate) {
                return $candidate
            }
        }
    }

    return ''
}

function Wait-ForServiceStatus {
    param(
        [string]$Name,
        [string]$DesiredStatus = 'Running',
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
        if ($service -and "$($service.Status)" -eq $DesiredStatus) {
            return $true
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Install-LocalMySqlIfNeeded {
    param(
        [string]$WingetId,
        [string]$PreferredServiceName
    )

    $mysqldPath = Resolve-MySqlBinary -BinaryName 'mysqld.exe' -PreferredServiceName $PreferredServiceName
    if ($mysqldPath) {
        Write-Step "MySQL binaries already present: $mysqldPath"
        return $mysqldPath
    }

    if (-not (Test-IsAdministrator)) {
        throw 'Local MySQL installation requires an elevated PowerShell session.'
    }

    Write-Step "Installing MySQL with winget package $WingetId"
    Invoke-Winget -Arguments @(
        'install',
        '--id', $WingetId,
        '--exact',
        '--source', 'winget',
        '--accept-source-agreements',
        '--accept-package-agreements',
        '--disable-interactivity',
        '--silent'
    )

    $mysqldPath = Resolve-MySqlBinary -BinaryName 'mysqld.exe' -PreferredServiceName $PreferredServiceName
    if (-not $mysqldPath) {
        throw 'MySQL installation completed but mysqld.exe was not found.'
    }

    return $mysqldPath
}

function Ensure-LocalMySqlConfigured {
    param(
        [string]$PreferredServiceName,
        [string]$RootPassword,
        [string]$ConfigType,
        [int]$Port
    )

    $existingService = Get-ExistingMySqlService -PreferredName $PreferredServiceName
    if ($existingService) {
        if ($existingService.State -ne 'Running') {
            Write-Step "Starting existing MySQL service: $($existingService.Name)"
            Start-Service -Name $existingService.Name
            [void](Wait-ForServiceStatus -Name $existingService.Name -DesiredStatus 'Running' -TimeoutSeconds 60)
        } else {
            Write-Step "MySQL service already configured: $($existingService.Name)"
        }
        return $existingService.Name
    }

    if (-not (Test-IsAdministrator)) {
        throw 'Local MySQL configuration requires an elevated PowerShell session.'
    }

    $configurator = Resolve-MySqlBinary -BinaryName 'mysql_configurator.exe' -PreferredServiceName $PreferredServiceName
    if (-not $configurator) {
        throw 'mysql_configurator.exe not found after MySQL installation.'
    }

    $serviceName = if ($PreferredServiceName) { $PreferredServiceName } else { 'MySQL84' }
    Write-Step "Configuring MySQL service $serviceName on port $Port"
    $previousPassword = $env:MYSQL_PWD
    try {
        $env:MYSQL_PWD = $RootPassword
        & $configurator @(
            '--console',
            '--action=configure',
            "--config-type=$ConfigType",
            '--enable-tcp-ip=true',
            "--port=$Port",
            '--configure-as-service=true',
            "--windows-service-name=$serviceName",
            '--windows-service-auto-start=true',
            '--open_win_firewall=true',
            '--install-sample-database=None'
        ) 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) {
            throw "mysql_configurator.exe failed with exit code $LASTEXITCODE"
        }
    } finally {
        if ($null -eq $previousPassword) {
            Remove-Item Env:MYSQL_PWD -ErrorAction SilentlyContinue
        } else {
            $env:MYSQL_PWD = $previousPassword
        }
    }

    if (-not (Wait-ForServiceStatus -Name $serviceName -DesiredStatus 'Running' -TimeoutSeconds 90)) {
        $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($service -and $service.Status -ne 'Running') {
            Start-Service -Name $serviceName
            [void](Wait-ForServiceStatus -Name $serviceName -DesiredStatus 'Running' -TimeoutSeconds 60)
        }
    }

    return $serviceName
}

function Test-CondaEnv {
    param([string]$Name)

    try {
        $json = & $script:CondaCommand env list --json | Out-String
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        $data = $json | ConvertFrom-Json
        foreach ($prefix in ($data.envs | ForEach-Object { "$_" })) {
            if ((Split-Path $prefix -Leaf) -ieq $Name) {
                return $true
            }
        }
        return $false
    } catch {
        return $false
    }
}

function Get-CondaPythonPath {
    param([string]$Name)

    $pythonPath = & $script:CondaCommand run -n $Name python -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to resolve python for conda env: $Name"
    }
    return (($pythonPath | Select-Object -First 1).Trim())
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
        $name = $trimmed.Substring(0, $idx).Trim().ToUpperInvariant()
        if ($name -ne $target) {
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

function Get-EnvPathEntries {
    param([string]$EnvPrefix)

    return @(
        $EnvPrefix
        (Join-Path $EnvPrefix 'Library\bin')
        (Join-Path $EnvPrefix 'Library\sbin')
        (Join-Path $EnvPrefix 'Scripts')
        (Join-Path $EnvPrefix 'bin')
    )
}

function Test-Executable {
    param([string]$Path)
    return [bool](Test-Path $Path)
}

function Invoke-TempPythonScript {
    param(
        [string]$PythonPath,
        [string]$ScriptText
    )

    $tempFile = Join-Path $env:TEMP ("codex_install_" + [guid]::NewGuid().ToString('N') + ".py")
    $exitCode = 0
    try {
        Set-Content -Path $tempFile -Value $ScriptText -Encoding UTF8
        & $PythonPath $tempFile 2>&1 | ForEach-Object { Write-Host $_ }
        $exitCode = $LASTEXITCODE
    } finally {
        Remove-Item $tempFile -ErrorAction SilentlyContinue
    }
    return $exitCode
}

$serverRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$requirementsFile = Join-Path $serverRoot 'requirements-lock.txt'
$envFile = Join-Path $serverRoot '.env'
$envMySqlHost = Get-EnvValue -Path $envFile -Key 'MYSQL_HOST' -DefaultValue 'localhost'
$envMySqlPort = Get-EnvValue -Path $envFile -Key 'MYSQL_PORT' -DefaultValue '3306'
$envMySqlUser = Get-EnvValue -Path $envFile -Key 'MYSQL_USER' -DefaultValue 'root'
$envMySqlPassword = Get-EnvValue -Path $envFile -Key 'MYSQL_PASSWORD' -DefaultValue 'root'
$envMySqlDatabase = Get-EnvValue -Path $envFile -Key 'MYSQL_DATABASE' -DefaultValue 'loognix'
$script:WingetCommand = Resolve-WingetCommand
$desiredMySqlServiceName = if ($MySqlServiceName) { $MySqlServiceName } else { 'MySQL84' }
$desiredMySqlRootPassword = if ($MySqlRootPassword) { $MySqlRootPassword } elseif ($envMySqlPassword) { $envMySqlPassword } else { 'root' }
$desiredMySqlPort = 3306
try {
    $desiredMySqlPort = [int]$envMySqlPort
} catch {
    $desiredMySqlPort = 3306
}
$localMySqlHostNames = @('localhost', '127.0.0.1', '::1', '.')
$manageLocalMySql = (-not $SkipMySqlInstall) -and ($localMySqlHostNames -contains ($envMySqlHost.ToLowerInvariant()))

$script:CondaCommand = Resolve-CondaCommand -PreferredPath $CondaExe
if (-not $script:CondaCommand) {
    throw 'conda.exe not found. Set -CondaExe or install/initialize Anaconda first.'
}

Write-Step "Server root: $serverRoot"
Write-Step "Conda command: $script:CondaCommand"
if ($script:WingetCommand) {
    Write-Step "winget command: $script:WingetCommand"
}
Write-Step "MySQL target host: $envMySqlHost"
Write-Step "MySQL database: $envMySqlDatabase"

if (-not (Test-Path $requirementsFile)) {
    throw "requirements-lock.txt not found: $requirementsFile"
}

if (-not (Test-CondaEnv -Name $EnvName)) {
    Write-Step "Creating conda environment $EnvName (python=$PythonVersion)"
    Invoke-Conda -Arguments @('create', '-n', $EnvName, '-y', "-c", 'conda-forge', "python=$PythonVersion")
} else {
    Write-Step "Conda environment already exists: $EnvName"
}

$condaPackages = @(
    'ffmpeg'
    'gstreamer'
    'gst-plugins-base'
    'gst-plugins-good'
    'gst-plugins-bad'
    'gst-plugins-ugly'
    'gst-rtsp-server'
    'glib'
    'glib-tools'
    'glib-networking'
    'pygobject'
    'pycairo'
    'mosquitto'
)

if (-not $SkipCondaPackages) {
    Write-Step "Installing media/runtime packages with conda"
    $condaInstallArgs = @('install', '-n', $EnvName, '-y', '-c', 'conda-forge') + $condaPackages
    Invoke-Conda -Arguments $condaInstallArgs
} else {
    Write-Step 'Skipping conda package installation'
}

$pythonPath = Get-CondaPythonPath -Name $EnvName
$envPrefix = Split-Path -Parent $pythonPath
$envPathPrefix = (Get-EnvPathEntries -EnvPrefix $envPrefix) -join ';'
$env:PATH = "$envPathPrefix;$env:PATH"
$env:CONDA_PREFIX = $envPrefix
$env:PYTHONIOENCODING = 'utf-8'

Write-Step "Environment python: $pythonPath"
Write-Step "Environment prefix: $envPrefix"

if (-not $SkipPipInstall) {
    Write-Step 'Upgrading pip'
    & $pythonPath -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw 'pip upgrade failed'
    }

    Write-Step 'Installing Python dependencies from requirements-lock.txt'
    & $pythonPath -m pip install -r $requirementsFile
    if ($LASTEXITCODE -ne 0) {
        throw 'pip install -r requirements-lock.txt failed'
    }
} else {
    Write-Step 'Skipping pip dependency installation'
}

if ($manageLocalMySql) {
    Write-Step 'Ensuring local MySQL is installed and configured'
    $null = Install-LocalMySqlIfNeeded -WingetId $MySqlWingetId -PreferredServiceName $desiredMySqlServiceName
    $configuredService = Ensure-LocalMySqlConfigured `
        -PreferredServiceName $desiredMySqlServiceName `
        -RootPassword $desiredMySqlRootPassword `
        -ConfigType $MySqlConfigType `
        -Port $desiredMySqlPort
    Write-Step "Local MySQL ready via service: $configuredService"
} elseif ($SkipMySqlInstall) {
    Write-Step 'Skipping local MySQL installation/configuration'
} else {
    Write-Step 'MYSQL_HOST is not local, skipping local MySQL installation/configuration'
}

if ((-not $SkipDatabaseCheck) -and (Test-Path $envFile)) {
    Write-Step 'Checking MySQL connectivity and creating database if reachable'
    $envFileForPython = $envFile.Replace('\', '\\')
    $dbScript = @"
from dotenv import dotenv_values
import pymysql
import os

cfg = dotenv_values(r'''$envFileForPython''')
configured_host = cfg.get("MYSQL_HOST", "localhost")
host = "localhost" if os.environ.get("INSTALL_LOCAL_MYSQL") == "1" else configured_host
port = int(cfg.get("MYSQL_PORT", 3306))
user = cfg.get("MYSQL_USER", "root")
password = cfg.get("MYSQL_PASSWORD", "root")
database = cfg.get("MYSQL_DATABASE", "loognix")
root_password = os.environ.get("INSTALL_MYSQL_ROOT_PASSWORD") or password or "root"
require_ready = os.environ.get("INSTALL_MYSQL_REQUIRE_READY") == "1"

def quote_identifier(value: str) -> str:
    tick = chr(96)
    return tick + str(value).replace(tick, tick * 2) + tick

grant_host = configured_host
if configured_host in {"localhost", "127.0.0.1", "::1", "."}:
    grant_host = configured_host
elif not configured_host:
    grant_host = "%"

try:
    conn = pymysql.connect(
        host=host,
        port=port,
        user="root",
        password=root_password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
    )
except Exception as exc:
    print(f"DB_CHECK_WARN {exc}")
    raise SystemExit(1 if require_ready else 0)

with conn.cursor() as cur:
    cur.execute("SELECT VERSION()")
    print("MYSQL_OK", cur.fetchone()[0])
    cur.execute(
        f"CREATE DATABASE IF NOT EXISTS {quote_identifier(database)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    print("DB_READY", database)
    if user and user != "root":
        cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (user, grant_host, password))
        cur.execute("ALTER USER %s@%s IDENTIFIED BY %s", (user, grant_host, password))
        cur.execute(
            f"GRANT ALL PRIVILEGES ON {quote_identifier(database)}.* TO %s@%s",
            (user, grant_host),
        )
        cur.execute("FLUSH PRIVILEGES")
        print("APP_USER_READY", f"{user}@{grant_host}")

conn.close()
"@
    $previousLocalMySql = $env:INSTALL_LOCAL_MYSQL
    $previousMySqlRootPassword = $env:INSTALL_MYSQL_ROOT_PASSWORD
    $previousRequireReady = $env:INSTALL_MYSQL_REQUIRE_READY
    try {
        $env:INSTALL_LOCAL_MYSQL = if ($manageLocalMySql) { '1' } else { '0' }
        $env:INSTALL_MYSQL_ROOT_PASSWORD = $desiredMySqlRootPassword
        $env:INSTALL_MYSQL_REQUIRE_READY = if ($manageLocalMySql) { '1' } else { '0' }
        $dbExitCode = Invoke-TempPythonScript -PythonPath $pythonPath -ScriptText $dbScript
    } finally {
        if ($null -eq $previousLocalMySql) {
            Remove-Item Env:INSTALL_LOCAL_MYSQL -ErrorAction SilentlyContinue
        } else {
            $env:INSTALL_LOCAL_MYSQL = $previousLocalMySql
        }
        if ($null -eq $previousMySqlRootPassword) {
            Remove-Item Env:INSTALL_MYSQL_ROOT_PASSWORD -ErrorAction SilentlyContinue
        } else {
            $env:INSTALL_MYSQL_ROOT_PASSWORD = $previousMySqlRootPassword
        }
        if ($null -eq $previousRequireReady) {
            Remove-Item Env:INSTALL_MYSQL_REQUIRE_READY -ErrorAction SilentlyContinue
        } else {
            $env:INSTALL_MYSQL_REQUIRE_READY = $previousRequireReady
        }
    }
    if ($dbExitCode -ne 0) {
        throw 'MySQL connectivity check failed'
    }
} elseif ($SkipDatabaseCheck) {
    Write-Step 'Skipping MySQL/database check'
} else {
    Write-Step 'No .env found, skipping MySQL/database check'
}

Write-Step 'Validating FFmpeg'
$ffmpegExe = Join-Path $envPrefix 'Library\bin\ffmpeg.exe'
if (-not (Test-Executable $ffmpegExe)) {
    throw "ffmpeg.exe not found: $ffmpegExe"
}
& $ffmpegExe -v error -f lavfi -i "testsrc=size=320x240:rate=5" -t 1 -f null -
if ($LASTEXITCODE -ne 0) {
    throw 'FFmpeg video smoke test failed'
}
& $ffmpegExe -v error -f lavfi -i "sine=frequency=1000:sample_rate=16000" -t 1 -f null -
if ($LASTEXITCODE -ne 0) {
    throw 'FFmpeg audio smoke test failed'
}

Write-Step 'Validating GStreamer CLI'
$gstLaunchExe = Join-Path $envPrefix 'Library\bin\gst-launch-1.0.exe'
if (-not (Test-Executable $gstLaunchExe)) {
    throw "gst-launch-1.0.exe not found: $gstLaunchExe"
}
& $gstLaunchExe -q videotestsrc num-buffers=10 ! videoconvert ! fakesink
if ($LASTEXITCODE -ne 0) {
    throw 'GStreamer video smoke test failed'
}
& $gstLaunchExe -q audiotestsrc num-buffers=20 ! audioconvert ! audioresample ! fakesink
if ($LASTEXITCODE -ne 0) {
    throw 'GStreamer audio smoke test failed'
}

Write-Step 'Validating Python GStreamer bindings'
$gstPythonScript = @"
import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import Gst, GstRtspServer
Gst.init(None)
print("PY_GST_OK", Gst.version_string())
print("PY_GST_RTSP_OK", hasattr(GstRtspServer, "RTSPServer"))
"@
$gstExitCode = Invoke-TempPythonScript -PythonPath $pythonPath -ScriptText $gstPythonScript
if ($gstExitCode -ne 0) {
    throw 'Python GStreamer validation failed'
}

Write-Step 'Installation completed successfully'
Write-Host ''
Write-Host 'Next steps:'
Write-Host "1. Update $envFile with the target device's MQTT/RTSP settings if needed."
Write-Host "2. Current MySQL target: host=$envMySqlHost database=$envMySqlDatabase user=$envMySqlUser"
Write-Host "3. Start API/MQTT with:"
Write-Host "   powershell -ExecutionPolicy Bypass -File `"$serverRoot\scripts\conda_run.ps1`" -EnvName `"$EnvName`" -Mode `"stack`" -CondaExe `"$script:CondaCommand`""
Write-Host "4. Check:"
Write-Host '   http://127.0.0.1:8000/api/health'
Write-Host '   http://127.0.0.1:8000/api/health/ready'
