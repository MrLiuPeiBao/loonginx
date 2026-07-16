# Server Deployment Guide

> 当前 Server + Web 新电脑部署请优先使用
> `../docs/WINDOWS_SERVER_WEB_DEPLOYMENT.md` 和 `../deploy/windows/`。本文保留 Server
> 单组件安装参数说明；不要把本机 `.env`、相机凭据或 TLS 私钥提交到仓库。

This document describes how to install and start the `server` component on a new Windows machine that already has Anaconda installed.

## Scope

This guide is for:

- Windows
- Anaconda already installed
- Deploying the `server/` project locally
- Optional local MySQL installation by the deployment script

## Files

Deployment uses these scripts:

- `scripts/install_from_anaconda.ps1`
- `scripts/conda_run.ps1`

## Prerequisites

Before running the installation script, make sure:

- Anaconda is installed
- You know the full path to `conda.exe`
- PowerShell can run local scripts
- If you want the script to install/configure local MySQL, run PowerShell as Administrator

Typical `conda.exe` examples:

```powershell
E:\anaconda\Scripts\conda.exe
C:\ProgramData\anaconda3\Scripts\conda.exe
C:\Users\<your-user>\anaconda3\Scripts\conda.exe
```

## What The Install Script Does

`scripts/install_from_anaconda.ps1` will:

1. Find or create the Conda environment `sensor_server`
2. Install runtime packages with Conda:
   - `ffmpeg`
   - `gstreamer`
   - `gst-plugins-base`
   - `gst-plugins-good`
   - `gst-plugins-bad`
   - `gst-plugins-ugly`
   - `gst-rtsp-server`
   - `glib`
   - `glib-tools`
   - `glib-networking`
   - `pygobject`
   - `pycairo`
   - `mosquitto`
3. Run `pip install -r requirements-lock.txt`
4. If `.env` uses a local MySQL host such as `localhost` or `127.0.0.1`:
   - install MySQL with `winget` when missing
   - configure the Windows MySQL service
   - create the target database
   - create/update the application user when `MYSQL_USER` is not `root`
5. Validate:
   - FFmpeg
   - GStreamer CLI
   - Python `Gst` and `GstRtspServer`

## Recommended Install Command

Open an elevated PowerShell window, then run:

```powershell
cd C:\path\to\loonginx\server

powershell -ExecutionPolicy Bypass -File .\scripts\install_from_anaconda.ps1 `
  -CondaExe "E:\anaconda\Scripts\conda.exe" `
  -MySqlRootPassword "root"
```

Replace the `-CondaExe` path with the actual path on the target machine.

## Important Behavior

- If `.env` contains `MYSQL_HOST=localhost` or `MYSQL_HOST=127.0.0.1`, the script treats MySQL as local and will try to install/configure it unless `-SkipMySqlInstall` is used.
- If `.env` points to a remote MySQL host, the script will skip local MySQL installation and only perform connectivity/database checks.
- Local MySQL installation/configuration requires Administrator privileges.

## Common Parameters

```powershell
-CondaExe           Full path to conda.exe
-EnvName            Conda environment name, default: sensor_server
-PythonVersion      Python version for the Conda environment, default: 3.11
-MySqlServiceName   Preferred Windows MySQL service name
-MySqlRootPassword  Root password used for local MySQL configuration
-MySqlWingetId      winget package id, default: Oracle.MySQL
-MySqlConfigType    Developer | Server | Dedicated | Manual
-SkipMySqlInstall   Skip local MySQL installation/configuration
-SkipCondaPackages  Skip Conda package installation
-SkipPipInstall     Skip pip install
-SkipDatabaseCheck  Skip MySQL connectivity/database creation
```

Show built-in help:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_from_anaconda.ps1 -Help
```

## Example Commands

Full install:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_from_anaconda.ps1 `
  -CondaExe "E:\anaconda\Scripts\conda.exe" `
  -MySqlRootPassword "root"
```

Use an existing environment and skip package installation:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_from_anaconda.ps1 `
  -CondaExe "E:\anaconda\Scripts\conda.exe" `
  -SkipCondaPackages `
  -SkipPipInstall
```

Skip local MySQL installation:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_from_anaconda.ps1 `
  -CondaExe "E:\anaconda\Scripts\conda.exe" `
  -SkipMySqlInstall
```

## Configure `.env`

Before or after installation, review these values in `.env`:

```powershell
MYSQL_HOST
MYSQL_PORT
MYSQL_USER
MYSQL_PASSWORD
MYSQL_DATABASE

MQTT_BROKER
MQTT_PORT
MQTT_USERNAME
MQTT_PASSWORD

API_HOST
API_PORT

YOLO_ENABLED
YOLO_RTSP_INPUT
YOLO_MODEL_PATH

AUDIO_ENABLED
AUDIO_RTSP_INPUT
```

Notes:

- For local database deployment, keep `MYSQL_HOST=localhost`
- For LAN access, typically use `API_HOST=0.0.0.0`
- If cameras or audio streams are not ready yet, you can temporarily disable:
  - `YOLO_ENABLED=false`
  - `AUDIO_ENABLED=false`

## Start The Server

After installation finishes, start the full local stack with:

```powershell
cd C:\path\to\loonginx\server

powershell -ExecutionPolicy Bypass -File .\scripts\conda_run.ps1 `
  -EnvName "sensor_server" `
  -Mode "stack" `
  -CondaExe "E:\anaconda\Scripts\conda.exe"
```

Available modes:

- `stack`: start MQTT, API, and GUI
- `api`: start API only
- `gui`: start GUI only
- `mqtt`: start MQTT only

Show help:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\conda_run.ps1 -Help
```

## Validation

After startup, check:

- `http://127.0.0.1:8000/api/health`
- `http://127.0.0.1:8000/api/health/ready`
- `http://127.0.0.1:8000/docs`

Expected result:

- `/api/health` returns `status=ok`
- `/api/health/ready` returns HTTP `200`

## Deployment Notes Learned From This Machine

- Do not assume `conda` is already in `PATH`; pass `-CondaExe` when possible
- The runtime must inherit the Conda environment `PATH`, especially:
  - `Library/bin`
  - `Library/sbin`
  - `Scripts`
- FFmpeg and GStreamer may be installed correctly but still fail if the process is started without the Conda DLL paths
- `mosquitto.exe` can be started directly from the Conda environment
- Validating with `/api/health` and `/api/health/ready` is the fastest deployment check

## Troubleshooting

### 1. `conda.exe not found`

Use the full path:

```powershell
-CondaExe "E:\anaconda\Scripts\conda.exe"
```

### 2. MySQL install/configure fails

Check:

- PowerShell was opened as Administrator
- `winget` works on the machine
- `.env` uses the expected MySQL host and port

If you do not want the script to manage local MySQL:

```powershell
-SkipMySqlInstall
```

### 3. GStreamer DLL errors

This usually means the process was started without the Conda environment DLL paths. Start through:

- `scripts/conda_run.ps1`
- or a shell where the Conda environment is already activated

### 4. API starts but `/ready` is not healthy

Check:

- MySQL service is running
- MQTT broker is running
- `.env` values are correct

## Suggested First Run On A New Machine

```powershell
cd C:\path\to\loonginx\server

powershell -ExecutionPolicy Bypass -File .\scripts\install_from_anaconda.ps1 `
  -CondaExe "E:\anaconda\Scripts\conda.exe" `
  -MySqlRootPassword "root"

powershell -ExecutionPolicy Bypass -File .\scripts\conda_run.ps1 `
  -EnvName "sensor_server" `
  -Mode "stack" `
  -CondaExe "E:\anaconda\Scripts\conda.exe"
```
