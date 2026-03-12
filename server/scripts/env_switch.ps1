param(
    [ValidateSet('realtime', 'quality')]
    [string]$Mode = 'realtime',
    [switch]$Help,
    [string]$EnvPath = 'server/.env',
    [string]$ProfileDir = 'server/env_profiles',
    [string]$CommonPath = 'server/env_profiles/common.env'
)

$ErrorActionPreference = 'Stop'

if ($Help) {
    Write-Host @"
用法:
  powershell -ExecutionPolicy Bypass -File env_switch.ps1 -Mode realtime|quality

说明:
  - 会将 env_profiles/common.env 与指定 profile 合并生成 .env
  - 旧 .env 会自动备份为 .env.bak_时间戳

新增可选配置（在 common.env 里设置即可生效）:
  COMMAND_TIMEOUT_SECONDS   命令超时秒数
  PREFER_PAYLOAD_DEVICE_ID  是否优先使用 payload 内的 device_id
  YOLO_RUN_MODE             thread 或 process
  AUDIO_RUN_MODE            thread 或 process
  MEDIA_STORAGE_MODE        database 或 filesystem
  MEDIA_STORAGE_DIR         filesystem 模式保存目录
  MEDIA_STORE_IMAGE         是否保存图像
  MEDIA_STORE_AUDIO         是否保存音频
"@
    return
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\\..')

function Resolve-RepoPath {
    param([string]$PathValue)
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return Join-Path $repoRoot $PathValue
}

$envFullPath = Resolve-RepoPath $EnvPath
$profileFullPath = Resolve-RepoPath (Join-Path $ProfileDir "$Mode.env")
$commonFullPath = Resolve-RepoPath $CommonPath

if (-not (Test-Path $profileFullPath)) {
    Write-Error "Profile not found: $profileFullPath"
    exit 1
}

$commonLines = @()
if (-not (Test-Path $commonFullPath)) {
    Write-Error "Common env not found: $commonFullPath"
    exit 1
}
$commonLines = Get-Content -Path $commonFullPath

$profileLines = Get-Content -Path $profileFullPath
$profileMap = @{}
foreach ($line in $profileLines) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) {
        continue
    }
    if ($trimmed -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    $profileMap[$matches[1]] = $line
    }
}

$updated = New-Object System.Collections.Generic.List[string]
$seenKeys = New-Object System.Collections.Generic.HashSet[string]

foreach ($line in $commonLines) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
        $key = $matches[1]
        if ($profileMap.ContainsKey($key)) {
            $updated.Add($profileMap[$key])
            $seenKeys.Add($key) | Out-Null
            continue
        }
    }
    $updated.Add($line)
}

foreach ($key in $profileMap.Keys) {
    if (-not $seenKeys.Contains($key)) {
        $updated.Add($profileMap[$key])
    }
}

$backupPath = ''
if (Test-Path $envFullPath) {
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backupPath = "$envFullPath.bak_$timestamp"
    Copy-Item -Path $envFullPath -Destination $backupPath -Force
}

$updated | Set-Content -Path $envFullPath -Encoding UTF8
if ($backupPath) {
    Write-Host "Switched .env to profile '$Mode'. Backup: $backupPath"
} else {
    Write-Host "Created .env from common + profile '$Mode'."
}
