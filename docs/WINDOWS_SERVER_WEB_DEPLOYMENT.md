# Windows 新电脑部署教程（Server + Web）

本文是当前源码部署的权威入口，目标环境为 Windows 10/11 x64。部署后的浏览器统一访问
`http://<服务器局域网 IP>:8888/index`，不要让终端用户直接访问 `:8000`。

## 1. 部署组成

| 组件 | 目录 | 端口 | 说明 |
| --- | --- | --- | --- |
| Nginx + Web 静态文件 | `web/` | 8888 | 对外唯一 Web 入口，并反代 API、视频 WS、MQTT WS |
| FastAPI | `server/` | 8000 | 仅作为本机 Nginx 上游 |
| MySQL | 外部服务 | 3306 | 默认只允许本机访问 |
| Mosquitto MQTT | Conda 环境 | 1883 | 板端与 Server 的 MQTT TCP |
| MQTT WebSocket 代理 | `websocket/` | 1884 | 将浏览器 MQTT WS 转发到本机 1883 |
| go2rtc | `server/tools/go2rtc/` | 8554/1984 | 可选，相机统一入口与本机管理 API |
| YOLO RTSP 输出 | Server worker | 8555 | 可选媒体输出 |
| 视频 WebSocket sidecar | `websocket/` | 8089/8090 | 仅本机 Nginx 上游 |

当前 Web 是已经构建好的 Vue 3 静态产物，新电脑不需要重新编译前端。`websocket/` 仍需
Node.js 安装运行依赖。

## 2. 上线前准备

1. 为新电脑配置固定局域网 IP，并确认它能访问开发板、PLC 和相机。
2. 安装 Git 和 Git LFS。仓库中的 3D 模型通过 Git LFS 管理，缺少 LFS 会得到指针文件。
3. 安装 Anaconda 或 Miniconda，并准备 Python 3.11 环境。
4. 安装 Node.js 18 或更高版本，以及 FFmpeg，并确认 `node`、`npm`、`ffmpeg` 可执行。
5. 准备 MySQL root 管理密码，以及专用应用账号 `loonginx` 的强密码。
6. 轮换曾经在旧部署文件中出现过的相机口令。不要继续使用已经暴露的旧口令。

建议至少预留 20 GB 磁盘空间。媒体目录增长较快时，应把 `server/media` 放到独立数据盘，
并配置备份和保留策略。

## 3. 克隆部署分支

```powershell
git lfs install
git clone --branch codex/deploy-server-web-windows `
  https://github.com/MrLiuPeiBao/loonginx.git C:\loonginx
Set-Location C:\loonginx
git lfs pull
```

确认大模型不是 LFS 指针：

```powershell
Get-Item .\web\html\3dModules\robot3.glb | Select-Object Name,Length
git lfs ls-files
```

`robot3.glb` 应大于 100 MB。

## 4. 一次性安装

以管理员身份打开 PowerShell。先做最小 API + Web 部署，不要在第一次启动时同时启用
YOLO、音频和相机网关：

```powershell
Set-Location C:\loonginx
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\install.ps1 `
  -ServerIp "192.168.0.100" `
  -CondaExe "C:\Users\<用户名>\anaconda3\Scripts\conda.exe" `
  -ConfigureFirewall
```

把 `192.168.0.100` 改为新电脑的固定局域网 IP。省略数据库密码参数时，脚本会用安全输入
框提示，不会把密码显示在屏幕上。脚本会：

- 从安全模板创建被 Git 忽略的 `server/.env` 与 `websocket/.env`；
- 写入当前服务器的 Web/API/WS 地址；
- 创建或更新 Conda 环境 `sensor_server`；
- 安装 Python、FFmpeg、GStreamer、Mosquitto 等 Server 依赖；
- 执行 `npm ci --omit=dev`；
- 校验 Nginx 配置；
- 可选放行受信任专用网络的 8888/1883 入站端口。

安装结束后检查 `server/.env`。文件中的 `MYSQL_PASSWORD`、`MQTT_PASSWORD` 等只保留在
目标机，禁止加入 Git。

## 5. 第一次启动与验收

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\start.ps1 `
  -EnvName sensor_server `
  -CondaExe "C:\Users\<用户名>\anaconda3\Scripts\conda.exe" `
  -SkipWebsocket

Start-Sleep -Seconds 8
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\verify.ps1
```

浏览器访问 `http://<服务器 IP>:8888/index`。最小首启的通过标准：

- `/api/health/live` 返回 HTTP 200；
- `/api/health/ready` 返回 HTTP 200，且数据库和 MQTT 状态正常；
- `/api/runtime-config` 能通过 8888 反代返回；
- Web 首屏可加载，Network 中没有访问旧服务器 IP、`:8000`、`:8086` 或 `:8087`；
- 本机监听 8888、8000、1883；启动 sidecar 后再监听 1884、8089、8090。

如浏览器曾打开旧环境，前端会迁移常见旧地址。仍异常时，在浏览器站点数据中清除
`app_config`、`api_config`、`mqtt_config`、`video_config` 后刷新。

## 6. 启用相机、YOLO 与音频

先从模板创建本机 go2rtc 配置：

```powershell
New-Item -ItemType Directory -Force .\server\config\local | Out-Null
Copy-Item .\server\tools\go2rtc\go2rtc.example.yaml `
  .\server\config\local\go2rtc.yaml
notepad .\server\config\local\go2rtc.yaml
```

将模板中的相机 IP、用户名和密码替换为现场值。该目录已被 Git 忽略。然后依次修改
`server/.env`：

```dotenv
MEDIA_GATEWAY_ENABLED=true
YOLO_ENABLED=true
AUDIO_ENABLED=true
YOLO_COMPUTE_DEVICE=auto
```

如果还要使用 `http://<服务器 IP>:8888/static/demo.html` 的海康控制面板，需要另建它的
本机配置。此文件同样被 Git 忽略，不能提交真实凭据：

```powershell
New-Item -ItemType Directory -Force .\web\html\config\local | Out-Null
Copy-Item .\web\html\config\camera-config.example.js `
  .\web\html\config\local\camera-config.js
notepad .\web\html\config\local\camera-config.js
```

填写设备 Web 管理地址、端口和已轮换的账号密码，并把 `autoLogin` 改为 `true`。这份配置只
供 WebVideoCtrl 控制面板使用，不能代替 go2rtc 的 RTSP 配置。

`websocket/.env` 默认读取本机 `rtsp://127.0.0.1:8554/cam01` 和 `cam02`，避免多个进程
同时抢占相机 RTSP 会话。重新启动并执行完整媒体检查：

```powershell
.\deploy\windows\stop.ps1
.\deploy\windows\start.ps1 -EnvName sensor_server
Start-Sleep -Seconds 10
.\deploy\windows\verify.ps1 -RequireMediaSidecar
```

可额外用 FFmpeg 检查媒体流：

```powershell
ffmpeg -rtsp_transport tcp -i rtsp://127.0.0.1:8554/cam01 -t 1 -f null -
ffmpeg -rtsp_transport tcp -i rtsp://127.0.0.1:8555/yolo -t 1 -f null -
```

## 7. 数据库初始化与迁移

Server 启动时通过 SQLModel 自动创建缺失表、索引和兼容字段。本次部署没有改变数据库
模型，因此不需要额外 schema migration。生产数据不能通过 Git 搬迁。

从旧电脑迁移数据时，在旧电脑导出：

```powershell
mysqldump -u root -p --single-transaction --routines --triggers `
  --result-file=loognix.sql loognix
```

把文件通过受控介质传到新电脑，再导入：

```powershell
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS loognix CHARACTER SET utf8mb4"
cmd.exe /d /c 'mysql -u root -p loognix < "loognix.sql"'
```

媒体文件使用 `MEDIA_STORAGE_MODE=filesystem` 时，还要单独复制旧电脑的 `server/media`，
保持数据库记录中的相对路径不变。导入前后分别备份，先在非生产环境验证查询和索引。

## 8. 日常运维

启动：

```powershell
.\deploy\windows\start.ps1 -EnvName sensor_server
```

停止：

```powershell
.\deploy\windows\stop.ps1
```

常用日志：

- `server/logs/`：API、MQTT、媒体与运行时日志；
- `web/logs/error.log`：Nginx 错误；
- `websocket/server.out.log`、`server.err.log`：视频 sidecar；
- `server/media/`：文件系统媒体数据，不是日志。

需要开机自启时，使用 Windows 任务计划程序，以部署账户、最高权限和“计算机启动时”触发
运行 `deploy/windows/start.ps1`。先手工完成一次启动、停止和重启验收，再创建任务。

## 9. 网络与安全边界

- 对局域网只放行 8888（Web）和板端所需的 1883（MQTT TCP）。
- 8000、1884、1984、3306、8089、8090 默认只允许本机访问。
- 当前 Nginx 默认只启用 HTTP，适用于受信任的隔离局域网。HTTPS 证书和私钥必须由目标机
  独立配置，禁止提交到仓库。
- Mosquitto 示例配置允许匿名连接，只能用于受信任内网。跨网段或公网部署前必须启用认证、
  ACL、TLS 和防火墙白名单。
- Nginx 中保留海康 WebVideoCtrl 动态代理，同样不能暴露到公网。

## 10. 升级与回滚

升级前备份 `server/.env`、`websocket/.env`、`server/config/local`、
`web/html/config/local`、数据库和 `server/media`。然后：

```powershell
.\deploy\windows\stop.ps1
git fetch origin
git pull --ff-only
git lfs pull
conda run -n sensor_server python -m pip install -r .\server\requirements-lock.txt
Push-Location .\websocket; npm ci --omit=dev; Pop-Location
.\deploy\windows\start.ps1 -EnvName sensor_server
.\deploy\windows\verify.ps1
```

回滚使用已验证提交创建临时分支或 worktree，不要覆盖本地配置和数据目录。确认健康检查、Web
入口、数据库查询及媒体链路均通过后再恢复现场访问。

## 11. 常见故障

| 现象 | 排查 |
| --- | --- |
| `git lfs` 不存在 | 安装 Git LFS，执行 `git lfs install` 与 `git lfs pull` |
| Nginx 502 | 检查 8000/1884/8089/8090 上游是否监听，查看 `web/logs/error.log` |
| `/ready` 非 200 | 检查 MySQL、MQTT 和 `server/.env`，不要先启用媒体 worker |
| FFmpeg 找不到 | 安装 FFmpeg 或在 `websocket/.env` 设置绝对 `FFMPEG_PATH` |
| 相机 401/403 | 核对已轮换的相机账号、通道权限和 80/554 网络连通性 |
| 无 NVIDIA GPU | 保持 `YOLO_COMPUTE_DEVICE=auto` 或使用 `cpu` |
| 端口被占用 | 先运行 `stop.ps1`，再用 `Get-NetTCPConnection -LocalPort <端口>` 定位 |

旧 `build_scripts/` 打包方案不是本教程的部署入口：它尚未覆盖当前 worker 子进程、媒体网关和
配置键，不能替代以上源码 + Conda 部署流程。
