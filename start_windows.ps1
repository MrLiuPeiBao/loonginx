$ErrorActionPreference = 'Stop'

Set-Location -Path $PSScriptRoot

Write-Host '[INFO] Starting services (FastAPI + GUI) ...'

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.11 .\run_all.py
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python .\run_all.py
    exit $LASTEXITCODE
}

Write-Host '[ERROR] Python not found.'
Write-Host 'Please install Python 3.11 and ensure it is available in PATH (or install the Windows "py" launcher).'
exit 1

