# AGENTS.md

## Purpose

This file records repo-local operational knowledge that was established during live debugging.
Keep entries short, searchable, and stable.
Prefer updating this file with verified facts, not raw conversation history.

## Scope

- Repo in this directory: Windows upper-computer `server/` stack.
- External static frontend repo: `C:\Users\lpb\Desktop\coil-design-v2.1.1`
- External video relay/websocket repo: `C:\Users\lpb\Desktop\nodejs-ws`
- Common local host IP used in LAN integration: `192.168.0.100`
- Common camera IP used in LAN integration: `192.168.0.101`

## Quick Lookup

- API bind: `0.0.0.0:8000`
- Web entry via nginx: `127.0.0.1:8888` or `192.168.0.100:8888`
- MQTT broker: `:1883`
- RTSP relay input: `rtsp://127.0.0.1:8554/cam01`
- YOLO RTSP output: `rtsp://192.168.0.100:8555/yolo`
- Camera RTSP source: `rtsp://admin:<password>@192.168.0.101:554/Streaming/Channels/101`
- One-click start: `scripts\start_all.bat`
- One-click stop: `scripts\stop_all.bat`
- Conda stack start: `scripts\conda_run.ps1 -EnvName "sensor_server" -Mode "stack"`

## Ports

| Port | Owner | Notes |
| --- | --- | --- |
| `8000` | FastAPI / uvicorn | Internal API, nginx reverse-proxies here |
| `8888` | nginx in `coil-design-v2.1.1` | Browser entry for local and LAN clients |
| `8554` | `go2rtc` | Camera input relay only; do not bind YOLO output here |
| `8555` | YOLO RTSP output | Annotated stream output |
| `1883` | Mosquitto | MQTT broker |
| `8089` / `8090` | `nodejs-ws` | Video relay/websocket sidecar |
| `1984` | `go2rtc` HTTP API | Media gateway control/inspection |

## Cross-Repo Topology

- `server/` contains FastAPI, Tkinter GUI, MQTT ingestion, MySQL/SQLModel, YOLO, audio monitor.
- `coil-design-v2.1.1` is a separately deployed static frontend served by its own nginx.
- `nodejs-ws` is a separate relay sidecar; start/stop scripts in this repo are expected to manage it too.
- Do not assume frontend source code lives under `server/`; production frontend integration often requires editing the external nginx/static repo.

## Startup And Process Hygiene

- Prefer `scripts\start_all.bat` and `scripts\stop_all.bat` over ad hoc manual launches.
- `start_all.bat` is expected to bring up:
  - Conda server stack
  - nginx at `8888`
  - `nodejs-ws`
- `stop_all.bat` is expected to stop API, GUI, YOLO worker, audio worker, Mosquitto, nginx, and `nodejs-ws`.
- Prefer the unified scripts when restarting to avoid stale parent/child process trees.
- If `conda` is unreliable in `PATH`, use `conda run -n sensor_server ...` or the repo launch scripts instead of assuming shell activation is clean.

## Browser And API Access Rules

Keywords: `nginx`, `same-origin`, `API_BASE_URL`, `8888`, `runtime-config`

- Browser clients should enter through nginx on `8888`, not directly to `:8000`.
- Local browser:
  - `http://127.0.0.1:8888`
- Remote LAN browser:
  - `http://192.168.0.100:8888`
- Remote LAN browsers must never use `127.0.0.1` or `localhost` for API access.
- nginx in `coil-design-v2.1.1\conf\nginx.conf` proxies `/api/` to `http://127.0.0.1:8000`.
- `index.html` in the external frontend contains a localStorage migration script to rewrite stale cached API base URLs back to current origin.
- If a page loads but API calls fail on another host, check:
  - browser network requests still pointing at `127.0.0.1` or `:8000`
  - nginx `/api/` proxy
  - Windows firewall / LAN reachability

## RTSP / Media Topology

Keywords: `go2rtc`, `rtsp`, `cam01`, `8554`, `8555`, `MEDIA_GATEWAY`

- `go2rtc` listens on `:8554` and exposes stream name `cam01`.
- Verified `go2rtc` config pattern:
  - `cam01 -> rtsp://admin:<password>@192.168.0.101:554/Streaming/Channels/101`
- `8554` is reserved for relay input.
- YOLO output must use `8555`, not `8554`.
- When `YOLO_RTSP_OUTPUT` was bound to `8554`, audio and YOLO input consumers could hit the wrong server and return `404 Not Found`.
- Preferred input settings:
  - `YOLO_RTSP_INPUT=rtsp://127.0.0.1:8554/cam01`
  - `AUDIO_RTSP_INPUT=rtsp://127.0.0.1:8554/cam01`
- Preferred output setting:
  - `YOLO_RTSP_OUTPUT=rtsp://192.168.0.100:8555/yolo`

## Media Storage And API Stability

Keywords: `MEDIA_STORAGE_MODE`, `filesystem`, `database`, `timeout`

- `MEDIA_STORAGE_MODE=filesystem` is the safer operating mode for this stack.
- Database-backed media storage previously increased API timeout risk under audio/YOLO load.
- If the page alternates between "has data" and "service exception / no data", treat media pressure as a primary suspect before assuming frontend bugs.
- Useful health endpoints:
  - `/api/health/live`
  - `/api/health/ready`
  - `/api/health/workers`
  - `/api/gui/refresh`

## YOLO Operational Notes

Keywords: `YOLO_FPS`, `YOLO_INFERENCE_INTERVAL`, `YOLO_INFERENCE_SIZE`

- Aggressive YOLO settings can degrade API responsiveness and preview quality.
- Conservative starting point for stability:
  - `YOLO_FPS=8` to `10`
  - `YOLO_INFERENCE_INTERVAL=0.5`
  - `YOLO_INFERENCE_SIZE=512`
- If VLC cannot open `rtsp://192.168.0.100:8555/yolo`, check:
  - YOLO worker actually listening on `8555`
  - input relay on `8554` is healthy
  - RTSP source is reachable

## Audio Monitoring Notes

Keywords: `AUDIO_RTSP_INPUT`, `audio/metrics`, `rms_norm`, `wav_rms`, `wav_peak`, `spectral_available`

- Audio source must come from the camera RTSP stream, not the local microphone.
- Verified current path:
  - `AUDIO_RTSP_INPUT=rtsp://127.0.0.1:8554/cam01`
  - `cam01` carries both video and audio from the camera stream
- Verified via `ffprobe`:
  - audio codec: `pcm_alaw`
  - sample rate: `8000`
  - channels: `1`
- The backend metric stream at `/api/audio/metrics` reliably returns:
  - `rms_norm`
  - `wav_rms`
  - `wav_peak`
- `spectral_available=0` means spectral metrics did not compute; in that state do not trust:
  - `centroid`
  - `bandwidth`
  - `rolloff`
  - `flatness`
  - `flux`
- GUI audio trend in `app\gui\app.py` was adjusted to plot:
  - `rms_norm`
  - `wav_rms / 32768`
  - `wav_peak / 32768`
- If the GUI audio curve appears flat, first check whether it is still using spectral fields instead of the energy fields above.

## High-Value Files

- `app\api\__init__.py`: application wiring, worker creation, MQTT handler registration
- `app\api\routes.py`: HTTP API surface, runtime-config, aggregate GUI refresh
- `app\services\runtime_supervisor.py`: process lifecycle and sidecar supervision
- `app\services\audio_service.py`: RTSP audio capture and metrics
- `app\services\audio_metrics.py`: spectral metrics helper
- `app\services\yolo_service.py`: detection and snapshot logic
- `app\gui\app.py`: Tkinter GUI, including audio chart behavior
- `app\core\config.py`: `.env` parsing and runtime hot-update rules
- `..\coil-design-v2.1.1\conf\nginx.conf`: nginx reverse proxy for browser entry
- `..\coil-design-v2.1.1\html\index.html`: cached API base URL migration script

## PLC Boundary

Keywords: `plc`, `cableway`, `plc_rt`, `PLC_DIRECT_ENABLED`

- PLC / cableway control exists as a distinct subsystem in this codebase.
- Key PLC-related areas include:
  - `app\plc_rt\`
  - `app\runtime\plc_*`
  - `app\services\cableway_*`
  - `app\services\plc_*`
  - `app\schemas\cableway.py`
- Operationally, media/API issues are often unrelated to PLC.
- If working on API, GUI, audio, YOLO, nginx, or frontend issues, avoid touching PLC paths unless the task explicitly requires it.
- `PLC_DIRECT_ENABLED` has been used to disable direct PLC runtime while keeping the rest of the stack running.

## Known Failure Patterns

- `GET /api/*` intermittently timing out while `/api/health/live` still works:
  - business endpoints are blocked; investigate media pressure, DB queueing, or RTSP conflicts
- Audio log shows `DESCRIBE failed: 404 Not Found` for `rtsp://127.0.0.1:8554/cam01`:
  - wrong service owns `8554`, or relay path is wrong
- Page on another LAN host loads HTML but gets no API data:
  - cached API base URL still points to `127.0.0.1` / `localhost`
- YOLO output is reachable locally but not from LAN:
  - wrong bind address, firewall, or wrong output port

