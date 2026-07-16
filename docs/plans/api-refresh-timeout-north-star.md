# API 刷新超时与 RTSP 负载治理北极星规划

## 1. 背景

2026-05-07 14:25 至 14:26，GUI 日志连续出现：

- `数据刷新失败: API连接中断，可能正在重启或响应被中止`
- `数据刷新失败: API响应超时，请检查服务负载`

同一时间，服务端日志显示：

- API 在 14:25:50 至 14:25:52 仍能连续返回 `/api/sensors`、`/api/bms`、`/api/rfid`、`/api/images`、`/api/audio`、`/api/audio/metrics` 等接口的 `200 OK`。
- 音频 worker 从 14:25:50 开始反复调用 ffmpeg 拉取 `rtsp://192.168.0.101:554/Streaming/Channels/101`，每次都在约 5 至 6 秒后失败。
- YOLO worker 在 14:26:15 和 14:26:50 出现 OpenCV RTSP 打开超时，单次打开等待约 30 秒。
- `logs/audio.log` 已增长到约 172 MB，说明失败重试和日志写入已经形成持续噪声。

补充说明：后续现场检查时 `8000` 没有监听，是因为人工主动关闭 API；这个状态不能作为 14:25 那轮故障的直接根因。14:25 的根因需要以当时日志为准。

补充说明：现场确认摄像头当时没有启动。因此 RTSP 连接失败本身是预期现象；需要治理的是“摄像头离线”这一正常工况下，YOLO/音频 worker 不能高频阻塞重试，API 和 GUI 必须继续稳定工作。

## 2. 直接结论

本次 GUI 刷新失败不是单一接口写错，而是三个因素叠加：

1. 摄像头 RTSP 源在当时不可达或不稳定。
2. YOLO 与音频 worker 同时从同一个摄像头 RTSP 地址拉流，失败时各自进行阻塞式重试。
3. GUI 刷新任务串行请求多组 API，只要其中任意请求在 10 秒内没有返回，整轮刷新就被判定失败。

因此，用户看到的是“API 响应超时”，实际系统状态更接近：

```text
摄像头 RTSP 不可达
  -> ffmpeg/OpenCV 阻塞等待和频繁失败
  -> worker 资源、日志、RTSP 会话被持续消耗
  -> API 与 GUI 刷新同时抢占本机资源和数据库连接
  -> GUI 串行刷新中的某个请求超过 10 秒
  -> GUI 报 API响应超时
```

## 3. 证据链

### 3.1 GUI 串行刷新会放大单点超时

`C:\Users\lpb\Desktop\loonginx\server\app\gui\app.py` 中，`_refresh_data_task()` 当前按顺序请求：

- `/api/sensors`
- `/api/bms`
- `/api/rfid`
- `/api/commands`
- `/api/config/sensors`
- `/api/alarms`
- `/api/images`
- `/api/audio`
- `/api/audio/metrics`

每个请求默认 10 秒超时。串行方式的结果是：前面接口即使已经成功，只要后面任意一个接口超时，整轮仍然显示刷新失败。

### 3.2 音频拉流失败重试过于密集

`C:\Users\lpb\Desktop\loonginx\server\app\services\audio_service.py` 中，音频采集使用 ffmpeg：

- `-rtsp_transport tcp`
- `-timeout 5000000`
- 每次采集窗口 1 秒
- 失败后等待 2 秒继续下一轮

在 RTSP 源不可达时，实际表现为每约 7 秒失败一次。失败日志持续写入，日志文件快速膨胀。

### 3.3 YOLO 打开 RTSP 的阻塞时间较长

`C:\Users\lpb\Desktop\loonginx\server\app\services\yolo_service.py` 中，YOLO 在打开 RTSP 失败后重试。日志显示 OpenCV 触发：

```text
Stream timeout triggered after 30011 ms
Unable to open RTSP stream ..., retry in 5.0s
```

这意味着摄像头不可达时，YOLO worker 会周期性进入约 30 秒的阻塞打开过程。

### 3.4 当前配置让两个 worker 同时争用同一路 RTSP

`C:\Users\lpb\Desktop\loonginx\server\.env` 中：

```env
YOLO_ENABLED=true
YOLO_RUN_MODE=process
YOLO_RTSP_INPUT=rtsp://admin:<password>@192.168.0.101:554/Streaming/Channels/101

AUDIO_ENABLED=true
AUDIO_RUN_MODE=process
AUDIO_RTSP_INPUT=rtsp://admin:<password>@192.168.0.101:554/Streaming/Channels/101
```

YOLO 与音频已经是独立进程，这是正确方向；但二者仍直接连接同一个摄像头 RTSP。摄像头网络不稳定、RTSP 会话数限制、摄像头 554 端口短时不可达时，两个 worker 会同时进入失败重试。

## 4. 立即止血方案

这些动作目标是先让 API 和 GUI 稳定，代价是暂时降低或暂停视频/音频功能。

### 4.1 摄像头不可达时暂停 YOLO 和音频

如果现场 `192.168.0.101:554` 不可达，先在运行时配置或 `.env` 中关闭：

```env
YOLO_ENABLED=false
AUDIO_ENABLED=false
```

然后重启服务：

```powershell
C:\Users\lpb\Desktop\loonginx\server\scripts\stop_all.bat
C:\Users\lpb\Desktop\loonginx\server\scripts\start_all.bat
```

验收标准：

- `http://127.0.0.1:8888/api/runtime-config` 正常返回 JSON。
- GUI 连续刷新 10 轮无 `API响应超时`。
- `logs/audio.log` 不再快速增长。

### 4.2 单独验证摄像头 RTSP

在恢复 YOLO/音频前，先确认本机能连通摄像头 RTSP 端口：

```powershell
Test-NetConnection 192.168.0.101 -Port 554
```

再用 ffmpeg 做最小化验证：

```powershell
ffmpeg -rtsp_transport tcp -timeout 5000000 -i "rtsp://admin:<password>@192.168.0.101:554/Streaming/Channels/101" -t 3 -f null -
```

只有这两个验证通过，才应该打开 YOLO 和音频 worker。

### 4.3 降低失败日志量

短期可以把音频失败日志降频，例如同类 RTSP 失败 60 秒内只打印一次 warning，其余降为 debug。这样不会解决 RTSP 本身，但能避免日志 IO 继续拖慢系统。

### 4.4 避免 GUI 与网页同时高频刷新

现场排查阶段只保留一个前端入口：

- 要么使用 Tkinter GUI；
- 要么使用 `http://127.0.0.1:8888/` 网页；
- 不要同时打开多个网页标签页反复刷新。

## 5. 中期修复方案

### 5.1 GUI 刷新改为并发和部分成功

目标：某个接口慢，不拖垮整轮刷新。

改造点：

- 将 `_refresh_data_task()` 中 9 个串行 `await` 改为 `asyncio.gather(..., return_exceptions=True)`。
- 为不同接口设置不同超时：
  - 基础表格：3 秒
  - 图片列表：5 秒
  - 音频指标：5 秒
  - 命令接口：5 秒
- 单个接口失败时保留上一次成功数据，只在对应模块显示局部错误。

验收标准：

- 任一接口人为延迟 10 秒时，其他模块仍能刷新。
- GUI 不再因为单个接口超时显示整轮失败。

### 5.2 增加 GUI 聚合接口

新增一个后端接口：

```text
GET /api/gui/refresh
```

由 API 在服务端并发查询各模块数据，并返回：

```json
{
  "sensors": {"ok": true, "data": []},
  "bms": {"ok": true, "data": []},
  "rfid": {"ok": true, "data": []},
  "images": {"ok": false, "error": "timeout", "data": []},
  "audio_metrics": {"ok": true, "data": []}
}
```

优点：

- GUI 只发一个请求。
- 后端可统一限流、缓存、超时和降级。
- 日志能定位到底是哪个模块慢。

### 5.3 RTSP worker 增加断路器

YOLO 和音频 worker 都需要 RTSP 断路器：

- 先落地有界指数退避：失败后从 10 秒开始退避，最高 120 秒。
- 连续失败 3 次后再进入完整断路器 `OPEN` 状态。
- `OPEN` 状态下暂停重试 30 秒或更长。
- 再次探测时进入 `HALF_OPEN`。
- 探测成功后恢复 `CLOSED`。

建议初始参数：

```env
YOLO_RTSP_FAILURE_BACKOFF_INITIAL_SECONDS=10
YOLO_RTSP_FAILURE_BACKOFF_MAX_SECONDS=120
AUDIO_RTSP_FAILURE_BACKOFF_INITIAL_SECONDS=10
AUDIO_RTSP_FAILURE_BACKOFF_MAX_SECONDS=120
RTSP_FAILURE_THRESHOLD=3
RTSP_BACKOFF_INITIAL_SECONDS=10
RTSP_BACKOFF_MAX_SECONDS=120
RTSP_PROBE_TIMEOUT_SECONDS=3
```

验收标准：

- 摄像头断网 10 分钟内，worker 不应每 7 秒持续打满失败日志。
- 摄像头恢复后，worker 能在 2 分钟内自动恢复。

### 5.4 RTSP 输入统一接入层

不要让 YOLO、音频、网页中转服务器分别直连摄像头。北极星架构应变为：

```text
摄像头 192.168.0.101:554
  -> 本机媒体接入层 Media Gateway
      -> YOLO worker
      -> Audio worker
      -> nodejs-ws
      -> 调试播放器 VLC
```

候选实现：

- MediaMTX
- go2rtc
- FFmpeg 常驻 relay

目标：

- 摄像头只承受 1 个 RTSP 会话。
- 本机内部 fan-out。
- 摄像头断线由媒体接入层统一重连。

## 6. 长期北极星目标

北极星目标：API 必须始终是轻量控制面，RTSP、YOLO、音频、截图、转码都不能影响 API 的基础读写能力。

目标状态：

```text
API 控制面
  - 配置读取
  - 表格数据查询
  - 命令下发
  - 健康状态汇总

媒体计算面
  - RTSP 输入管理
  - YOLO 推理
  - 音频分析
  - 截图和 MQTT 发布
  - RTSP/WebSocket 输出

前端展示面
  - GUI
  - Web 8888
  - 局部刷新
  - 失败降级和状态提示
```

核心原则：

1. API 请求路径不能等待 RTSP。
2. 摄像头不可达不能拖慢数据库查询。
3. GUI 刷新必须允许部分成功。
4. worker 失败必须有退避、断路器和状态暴露。
5. 现场运维必须能一键查看 API、RTSP、worker、数据库、MQTT 的健康状态。

## 7. 里程碑计划

### M0：现场止血

时间：0.5 天

任务：

- 摄像头不可达时关闭 `YOLO_ENABLED` 和 `AUDIO_ENABLED`。
- 使用 `Test-NetConnection` 和 ffmpeg 验证 RTSP。
- 确认 GUI 在无媒体 worker 时刷新稳定。

验收：

- GUI 连续 10 轮刷新成功。
- API 基础接口 P95 小于 500 ms。

### M1：刷新链路降级

时间：1 天

任务：

- GUI 串行刷新改并发刷新。
- 单模块失败不影响整轮。
- 增加每个接口耗时日志。

验收：

- 任意一个接口超时，其他模块仍能显示最新或缓存数据。
- GUI 日志能写明具体失败接口，例如 `audio_metrics timeout`。

### M2：RTSP 断路器

时间：1 至 2 天

任务：

- 音频 worker 增加失败退避和日志限频。
- YOLO worker 增加打开失败退避。
- `/api/runtime/status` 暴露 worker 状态、失败次数、下次重试时间。

验收：

- 摄像头断开 10 分钟，API 仍可稳定响应。
- 日志不会以秒级频率重复输出同一 RTSP 错误。

### M3：聚合刷新接口

时间：1 至 2 天

任务：

- 新增 `/api/gui/refresh`。
- 后端内部并发查询并局部降级。
- GUI 默认走聚合接口，保留单接口刷新作为 fallback。

验收：

- GUI 一轮刷新只产生 1 个主请求。
- 后端日志能输出各模块耗时。

### M4：媒体接入层

时间：2 至 4 天

任务：

- 引入 MediaMTX 或等价本机 RTSP relay。
- 摄像头只被 relay 拉取一次。
- YOLO、音频、nodejs-ws 从 relay 拉流。

验收：

- 摄像头 RTSP 会话数稳定为 1。
- VLC、YOLO、音频、网页中转同时工作时不互相抢摄像头连接。

### M5：可观测性和运维

时间：1 至 2 天

任务：

- 增加 `/api/health/live`、`/api/health/ready`、`/api/health/workers`。
- 增加一键诊断脚本 `scripts\diagnose_all.ps1`。
- 增加日志轮转，限制单个日志文件大小。

验收：

- 一条命令能输出 API、MQTT、nginx、nodejs-ws、YOLO、音频、RTSP 摄像头状态。
- `logs/audio.log` 不再无限增长。

## 8. 推荐诊断命令

### 8.1 查看端口监听

```powershell
$ports = 8000,8888,8554,1883,8089,8090
foreach ($p in $ports) {
    Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
        Select-Object LocalAddress,LocalPort,OwningProcess
}
```

### 8.2 查看关键进程

```powershell
Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -match 'main:app|app\.gui\.app|app\.services\.yolo_worker|app\.services\.audio_worker|nodejs-ws|mosquitto|nginx'
    } |
    Select-Object ProcessId,ParentProcessId,Name,CommandLine
```

### 8.3 测 API 单接口耗时

```powershell
$urls = @(
    'http://127.0.0.1:8000/api/sensors?limit=20',
    'http://127.0.0.1:8000/api/bms?limit=20',
    'http://127.0.0.1:8000/api/images?limit=30',
    'http://127.0.0.1:8000/api/audio?limit=30',
    'http://127.0.0.1:8000/api/audio/metrics?limit=100'
)

foreach ($u in $urls) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 10
        $sw.Stop()
        [pscustomobject]@{ Status = $r.StatusCode; Ms = $sw.ElapsedMilliseconds; Url = $u }
    } catch {
        $sw.Stop()
        [pscustomobject]@{ Status = 'ERR'; Ms = $sw.ElapsedMilliseconds; Url = $u; Error = $_.Exception.Message }
    }
}
```

### 8.4 测摄像头 RTSP 端口

```powershell
Test-NetConnection 192.168.0.101 -Port 554
```

## 9. 风险与注意事项

- 不要单纯把 GUI timeout 从 10 秒调大。调大只能掩盖问题，会让用户等待更久。
- 不要让多个模块长期直连同一摄像头主码流。摄像头 RTSP 会话数和带宽都有限。
- 不要在 API 进程内执行阻塞型媒体计算。API 应只做控制面和查询面。
- 不要保留无限增长日志。当前 `audio.log` 已经证明日志 IO 会变成独立风险。

## 10. 最小成功标准

系统达到以下状态后，才算真正解决本类问题：

- 摄像头离线时，API 仍能稳定返回基础接口。
- GUI 能显示“摄像头离线/音频离线”，而不是笼统显示“API响应超时”。
- YOLO 和音频 worker 有明确状态、失败次数、下次重试时间。
- 日志文件有轮转和限频。
- 8888 网页、Tkinter GUI、nodejs-ws 同时打开时不会显著拖慢 API。
