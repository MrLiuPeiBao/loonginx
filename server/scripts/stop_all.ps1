param()

$ErrorActionPreference = 'SilentlyContinue'

function Stop-Tree {
    param(
        [int]$ProcessId,
        [hashtable]$Visited
    )
    if ($ProcessId -le 0 -or $Visited.ContainsKey($ProcessId)) {
        return
    }
    $Visited[$ProcessId] = $true
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Tree -ProcessId ([int]$_.ProcessId) -Visited $Visited }
    try {
        taskkill /PID $ProcessId /T /F | Out-Null
    } catch {
    }
}

$visited = @{}
$nginxRoot = 'C:\Users\lpb\Desktop\coil-design-v2.1.1'
$nginxExe = Join-Path $nginxRoot 'nginx.exe'
$nginxPidFile = Join-Path $nginxRoot 'logs\nginx.pid'
$nodeWsRoot = 'C:\Users\lpb\Desktop\nodejs-ws'

foreach ($port in @(1883, 8000, 8554, 8888, 8089, 8090)) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Tree -ProcessId ([int]$_) -Visited $visited }
}

if (Test-Path $nginxPidFile) {
    $masterPid = Get-Content $nginxPidFile | Select-Object -First 1
    if ($masterPid) {
        Stop-Tree -ProcessId ([int]$masterPid) -Visited $visited
    }
}

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) {
            return $false
        }
        return (
            $cmd -like '*main:app*' -or
            $cmd -like '*app.gui.app*' -or
            $cmd -like '*app.services.yolo_worker*' -or
            $cmd -like '*app.services.audio_worker*' -or
            $cmd -like '*mosquitto*'
        )
    } |
    ForEach-Object { Stop-Tree -ProcessId ([int]$_.ProcessId) -Visited $visited }

Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) {
            return $false
        }
        $normalized = $cmd -replace '/', '\'
        return $normalized -like '*nodejs-ws\index.js*' -or $normalized -like '*nodejs-ws\\index.js*'
    } |
    ForEach-Object { Stop-Tree -ProcessId ([int]$_.ProcessId) -Visited $visited }

Get-CimInstance Win32_Process -Filter "Name='nginx.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -eq $nginxExe } |
    ForEach-Object { Stop-Tree -ProcessId ([int]$_.ProcessId) -Visited $visited }

Get-CimInstance Win32_Process -Filter "Name='go2rtc.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Tree -ProcessId ([int]$_.ProcessId) -Visited $visited }

Write-Host '[stop_all] done'
