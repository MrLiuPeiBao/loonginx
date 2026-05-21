# 音视频解析与 API 控制面隔离北极星规划

## 1. 问题背景

现场日志显示摄像头已经能输出音视频：

```text
[AUDIO] ffmpeg capture finished rc=0
[AUDIO] ffmpeg output size=32106 bytes
[AUDIO] thresholds_all_zero=1.0 threshold_hit=1.0
[AUDIO] Audio threshold hit
[h264] corrupted macroblock ...
[YOLO] YOLO stream forwarded 59 frames, scheduled 0 inference jobs in 5.0s
```

这说明：

- 音频 RTSP 已经能拉到 1 秒 WAV。
- 音频阈值全为 0，当前逻辑会把音频片段按 `AUDIO_STORE_MIN_INTERVAL_SECONDS` 间隔持续保存。
- 视频 RTSP 能解码并转发，但 H264 解码持续出现损坏宏块。
- YOLO 在某个时段 5 秒内转发了 59 帧，但没有调度新的推理任务。

用户感知是“音视频流解析卡住 API”。从当前代码结构看，这个判断是合理的：媒体计算进程虽然已经从 API 主进程拆出，但媒体结果回传、落库、MQTT 发布仍然穿过 API 进程。

## 2. 当前架构实况

当前运行状态：

```text
API uvicorn :8000
  -> db-worker 单线程队列
  -> telemetry-bridge named pipe 服务
  -> mqtt-worker
  -> yolo_worker 子进程
  -> audio_worker 子进程

yolo_worker
  -> RTSP 摄像头
  -> OpenCV/YOLO 推理
  -> RTSP 输出 :8554
  -> telemetry named pipe
  -> API 进程
  -> MQTT + DB

audio_worker
  -> RTSP 摄像头
  -> ffmpeg 录 1 秒 WAV
  -> 计算音频指标
  -> telemetry named pipe
  -> API 进程
  -> DB + MQTT 告警
```

关键问题：

- `yolo_worker.py` 和 `audio_worker.py` 都通过 `TelemetryBridgeClient` 把结果发回 API。
- `TelemetryBridgeServer` 在 API 进程中处理 named pipe 消息。
- `TelemetryBridgeServer` 处理消息时同步执行 MQTT 发布和 DB 写入。
- `DBWorker` 是单线程队列，同时服务 API 查询和媒体写入。

因此，媒体解析虽然不在 API 进程里跑，但媒体结果写入仍然会回压 API。

## 3. 为什么音视频解析会卡住 API

### 3.1 telemetry bridge 是同步回传路径

worker 发送音频/图片时，会等待 API 进程返回响应：

```text
worker -> NamedPipeJsonClient.request()
       -> API TelemetryBridgeServer._handle_message()
       -> 写 DB / 发 MQTT
       -> 返回响应
```

`NamedPipeJsonClient` 默认等待响应。API 侧 handler 没处理完，worker 侧就会阻塞。

这会产生两个方向的影响：

- worker 等 API，导致媒体处理堆积。
- API 处理媒体消息，导致普通 API 查询排队。

### 3.2 DBWorker 是 API 与媒体共用的单线程队列

`DBWorker` 当前只有一个后台线程：

```text
API /api/sensors 查询
API /api/audio 查询
MQTT 传感器入库
YOLO 图片入库
Audio WAV 入库
运行时配置读写
```

这些任务都进入同一个队列。只要音频或 YOLO 写入变慢，普通 API 查询也会排在后面。GUI 的刷新接口即使只是读数据，也会被媒体写库影响。

### 3.3 音频阈值全为 0 会持续写音频

日志中：

```text
Audio thresholds snapshot={'centroid': 0.0, 'bandwidth': 0.0, 'rolloff': 0.0, 'flatness': 0.0, 'flux': 0.0, 'rms': 0.0}
All audio thresholds are 0; audio clips are stored at the configured interval
threshold_hit=1.0
```

当前行为是：所有阈值为 0 时，认为每个窗口都命中，只靠 `AUDIO_STORE_MIN_INTERVAL_SECONDS` 限频。

每个 1 秒 WAV 约 32 KB，当前回传路径还会把 WAV 转为 hex 字符串：

```text
32 KB WAV -> 64 KB hex -> named pipe -> API -> DB BLOB 或文件系统
```

如果间隔较短、MySQL 慢、磁盘 IO 忙、GUI 同时查 `/api/audio`，就会明显拖慢 API。

### 3.4 YOLO 推理线程可能被长期占用

日志：

```text
YOLO stream forwarded 59 frames, scheduled 0 inference jobs in 5.0s
```

代码中 YOLO 使用 `inference_busy` 控制推理并发。正常情况下，5 秒内应该调度若干次推理。出现 `scheduled 0 inference jobs` 的常见原因：

- 上一个 `model.track()` 仍未结束，`inference_busy` 一直为 true。
- GPU/CPU 推理过慢。
- H264 解码异常导致传入帧质量不稳定，推理或 tracker 被拖慢。
- tracker 状态异常或模型调用卡住。

这会导致视频仍然在转发，但检测截图和 MQTT 发布停止或延迟。

### 3.5 H264 解码错误说明输入流质量不稳定

日志中的：

```text
negative number of zero coeffs
corrupted macroblock
out of range intra chroma pred mode
Invalid level prefix
```

这些是 H264 码流解码错误，通常来自：

- 网络丢包或交换机/网线质量问题。
- 摄像头主码流码率过高。
- 摄像头编码参数不适合低延迟拉流。
- 同一路摄像头被多个消费者同时拉取。
- I 帧间隔太长，丢帧后恢复慢。

这些错误不一定直接让 API 慢，但会增加视频 worker 的解码和恢复成本，并提高 YOLO 推理卡住概率。

### 3.6 GUI 刷新串行放大问题

GUI 一轮刷新串行请求多组接口。只要 `/api/audio`、`/api/images` 或其他接口因为 DBWorker 排队慢下来，整轮刷新就会报：

```text
API响应超时，请检查服务负载
```

所以前端看到的是 API 超时，底层常常是媒体写入和 DB 队列回压。

## 4. 立即止血方案

### 4.1 音频不要用全 0 阈值长期运行

如果当前不是调试录音，建议设置一个有效 RMS 阈值。当前日志里的 `wav_rms=238.5`，可先用：

```env
AUDIO_THRESHOLD_RMS=500
```

这样普通底噪不会持续写库，只有 RMS 超过 500 时才保存音频片段。

如果只需要曲线，不需要保存音频片段，可以改为后续方案中的“指标和片段解耦”，短期先提高阈值。

### 4.2 媒体文件优先落盘，不直接塞数据库大字段

当前 `.env` 是：

```env
MEDIA_STORAGE_MODE=database
```

建议现场先改为：

```env
MEDIA_STORAGE_MODE=filesystem
MEDIA_STORAGE_DIR=media
MEDIA_STORE_IMAGE=true
MEDIA_STORE_AUDIO=true
```

这样数据库只保存路径，图片和 WAV 放到文件系统，DBWorker 压力会明显下降。

### 4.3 降低视频输入压力

建议摄像头侧先调整：

```text
分辨率：1280x720 或 960x540
FPS：10 到 15
码率：1024 到 2048 kbps
编码：H264
I 帧间隔：1 秒到 2 秒
码率控制：CBR
```

YOLO 侧建议：

```env
YOLO_FPS=10
YOLO_INFERENCE_INTERVAL=0.5
YOLO_INFERENCE_SIZE=512
YOLO_FRAME_WIDTH=960
YOLO_FRAME_HEIGHT=540
```

目标是先稳定，不追求最高帧率。

### 4.4 只保留一个摄像头直连消费者

当前可能同时存在：

- YOLO worker 拉摄像头。
- Audio worker 拉摄像头。
- nodejs-ws 拉摄像头。
- VLC 或其他工具拉摄像头。

短期排查时只保留必要消费者。多消费者会增加摄像头 RTSP 会话压力，也会放大 H264 解码错误。

## 5. 中期修复方案

### 5.1 媒体结果异步化，telemetry 不做同步重活

当前 telemetry handler 同步做：

- MQTT 发布。
- DB 写入。
- 告警发布。

应改为：

```text
worker -> telemetry -> API 只快速入队 -> 立即 ACK
后台 media-writer -> DB/MQTT/文件系统
```

API 接到 telemetry 后只做轻量校验和入队，不等待 MySQL 或 MQTT 完成。

验收标准：

- telemetry ACK P95 小于 50 ms。
- MySQL 慢 5 秒时，worker 不阻塞，API 查询不被拖慢。

### 5.2 DBWorker 拆分读写队列

当前单 DBWorker 需要拆成：

```text
db-read-worker
  -> GUI/API 查询

db-write-worker
  -> 传感器/MQTT 入库
  -> 音频/图片入库

media-write-worker
  -> 大对象写文件
  -> 媒体元数据写 DB
```

至少要把媒体大对象写入和普通 API 查询分开。

验收标准：

- 音频每 10 秒写入一次时，`/api/sensors`、`/api/runtime-config` P95 小于 500 ms。
- YOLO 连续截图时，`/api/audio/metrics` 不超时。

### 5.3 音频指标和音频片段解耦

音频模块应分成两条路径：

```text
每秒音频指标
  -> 内存 ring buffer
  -> /api/audio/metrics
  -> 不写 WAV

阈值命中音频片段
  -> 文件系统保存 WAV
  -> DB 只写元数据和路径
```

所有阈值为 0 时，建议语义改为：

- `AUDIO_DEBUG_STORE_ALL=true` 才保存所有片段。
- 阈值全 0 且 debug 关闭时，只保留指标，不保存 WAV。

这能避免“没有配置阈值等于持续写库”的意外行为。

### 5.4 YOLO 推理 watchdog

当前 `inference_busy` 如果被长时间占用，会导致后续不再调度推理。需要增加 watchdog：

```text
YOLO_INFERENCE_MAX_SECONDS=3
```

逻辑：

- 记录每次推理开始时间。
- 超过最大时间仍未释放，记录错误并允许下一次推理。
- 连续超时达到阈值时重启 YOLO worker。

验收标准：

- 日志不再长期出现 `scheduled 0 inference jobs`。
- 推理卡住后 10 秒内能自动恢复或重启 worker。

### 5.5 H264 输入稳定化

建议引入本机媒体接入层：

```text
摄像头 -> Media Gateway -> YOLO / Audio / nodejs-ws / VLC
```

候选：

- MediaMTX
- go2rtc
- FFmpeg 常驻 relay

摄像头只被一个进程拉取，内部再 fan-out 给各消费者。

验收标准：

- 摄像头端 RTSP 会话数稳定为 1。
- H264 解码错误显著减少。
- 多个前端打开时不影响 YOLO/音频稳定性。

## 6. 长期北极星目标

北极星目标：媒体计算面与 API 控制面彻底解耦。API 只负责配置、状态、查询和控制，不承担音视频大对象搬运和同步写入。

目标架构：

```text
摄像头
  -> Media Gateway
      -> yolo-service
      -> audio-service
      -> video-relay

yolo-service
  -> media artifact store
  -> event queue

audio-service
  -> metrics ring buffer
  -> media artifact store
  -> event queue

event queue
  -> media-writer
  -> mqtt-publisher
  -> alarm-service

API
  -> config/status/query
  -> read optimized DB/session
  -> no heavy media payload in request path
```

核心原则：

1. API 请求路径不等待 RTSP、ffmpeg、YOLO、MQTT、媒体文件写入。
2. 媒体大对象不通过 API 进程同步搬运。
3. 普通查询和媒体写入不共用一个单线程队列。
4. 音频指标默认轻量保留，音频片段只在阈值命中或调试模式保存。
5. YOLO 推理必须有 watchdog、超时、重启和状态上报。
6. 摄像头只被媒体接入层直连一次。

## 7. 里程碑计划

### M0：现场止血

时间：0.5 天

任务：

- 设置 `AUDIO_THRESHOLD_RMS=500` 或更高，避免底噪持续写 WAV。
- 设置 `MEDIA_STORAGE_MODE=filesystem`。
- 降低摄像头码率、FPS、I 帧间隔。
- 排查时只保留一个 RTSP 直连消费者。

验收：

- GUI 连续刷新 10 轮无 API 超时。
- `logs/audio.log` 不再秒级增长。
- `/api/runtime-config`、`/api/sensors` P95 小于 500 ms。

### M1：telemetry 快速 ACK

时间：1 天

任务：

- API telemetry handler 只入队并立即返回。
- 新增 `media_writer` 后台线程处理 DB/MQTT。
- 队列长度暴露到 `/api/runtime/status`。

验收：

- worker 发 telemetry 不再等待 DB/MQTT。
- MySQL 慢时 worker 不阻塞。

### M2：媒体存储重构

时间：1 至 2 天

任务：

- 默认使用 filesystem 保存图片/WAV。
- DB 只保存路径、元数据、指标。
- `/api/images/{id}/data` 和 `/api/audio/{id}/data` 按需从文件读取。

验收：

- image/audio 列表接口不读取大字段。
- 数据库体积增长速度明显下降。

### M3：DB 队列隔离

时间：1 至 2 天

任务：

- 拆分 read/write worker。
- API 查询使用读 worker。
- 媒体写入使用 media/write worker。

验收：

- 连续媒体写入时基础 API 不排队。
- GUI 刷新不受音频片段保存影响。

### M4：YOLO watchdog

时间：1 天

任务：

- 增加推理超时监控。
- 超时后跳过当前推理任务。
- 连续超时后重启 worker。

验收：

- `scheduled 0 inference jobs` 不持续超过 10 秒。
- YOLO 卡住能自恢复。

### M5：Media Gateway

时间：2 至 4 天

任务：

- 引入 MediaMTX 或 go2rtc。
- 摄像头只被 gateway 拉取。
- YOLO、音频、nodejs-ws 都从 gateway 拉流。

验收：

- H264 解码错误显著下降。
- VLC、网页、YOLO、音频同时使用时系统稳定。

## 8. 推荐立即配置

当前日志下，建议先改：

```env
MEDIA_STORAGE_MODE=filesystem
MEDIA_STORAGE_DIR=media
MEDIA_STORE_IMAGE=true
MEDIA_STORE_AUDIO=true

AUDIO_THRESHOLD_RMS=500
AUDIO_STORE_MIN_INTERVAL_SECONDS=30

YOLO_FPS=10
YOLO_INFERENCE_INTERVAL=0.5
YOLO_INFERENCE_SIZE=512
YOLO_FRAME_WIDTH=960
YOLO_FRAME_HEIGHT=540
```

重启：

```powershell
C:\Users\lpb\Desktop\loonginx\server\scripts\stop_all.bat
C:\Users\lpb\Desktop\loonginx\server\scripts\start_all.bat
```

## 9. 验证命令

### 9.1 API 耗时

```powershell
$urls = @(
  'http://127.0.0.1:8000/api/runtime-config',
  'http://127.0.0.1:8000/api/sensors?limit=20',
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

### 9.2 worker 与端口

```powershell
Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -match 'main:app|app\.services\.yolo_worker|app\.services\.audio_worker|nodejs-ws|mosquitto|nginx'
  } |
  Select-Object ProcessId,ParentProcessId,Name,CommandLine
```

### 9.3 H264 错误观察

```powershell
Get-Content C:\Users\lpb\Desktop\loonginx\server\logs\audio.log -Tail 100
```

如果持续出现 H264 解码错误，优先处理摄像头码率、I 帧间隔、RTSP 消费者数量和网线/交换机链路。

## 10. 最小成功标准

本问题真正解决的标准：

- 音视频解析高负载时，`/api/runtime-config` 和 `/api/sensors` 仍稳定响应。
- 音频底噪不会持续写 WAV。
- 图片和 WAV 不再默认作为大字段同步穿过 API 进程。
- YOLO 推理卡住能被 watchdog 发现并恢复。
- 摄像头只被本机媒体接入层拉取一次。
- GUI 看到的是“音频保存限流/YOLO 推理超时/摄像头码流异常”等具体状态，而不是笼统的 API 超时。
