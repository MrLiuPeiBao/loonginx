# RS-485 / Modbus RTU 轮询优化北极星规划

日期：2026-05-13
范围：下位机 `client` 在 `/dev/ttyS6` 上的 RS-485 / Modbus RTU 总线

## 1. 北极星目标

本项目的北极星不是把波特率拉到最高，而是让网关在一条共享 RS-485 总线上长期运行：某一个从站不响应、回包损坏或偶发离线时，整个 `client` 仍然继续采集其它设备、继续 MQTT 上报、继续输出心跳。

目标行为：

- `client` 可以连续运行数天，不因为单个 485 设备异常进入假死。
- 单个坏设备最多消耗自己的时间预算，不能拖住整条总线。
- CRC 错误、地址错位、缺 CRC、超时等问题按地址统计，而不是靠肉眼翻日志。
- 传感器数据允许按设备维度变成 `stale`，但网关整体不能变成 `stale`。
- IO 板输出必须边沿触发、合并、延迟、限次，不允许灯控写操作持续冲击总线。
- 24 小时稳定性测试里没有进程卡死、没有多实例抢串口、没有无界重试队列。

建议验收指标：

- `client` 连续运行：至少 24 小时无需人工重启。
- MQTT `device/hello`：按配置周期持续发布。
- MQTT `sensors/data`：健康分组按周期持续发布。
- 主循环停顿：p95 小于 10 秒，最坏小于 30 秒。
- 物理层整改后，健康地址成功率大于 98%。
- 物理层整改后，CRC/地址错位错误率低于 0.1%。
- IO 输出队列中没有超过 60 秒的旧动作，除非 IO 板已被明确标记为降级。

## 2. 当前系统图

已知运行事实：

- 主 RS-485 串口：`/dev/ttyS6`
- RFID 串口：`/dev/ttyS5`，不属于本文优化范围
- 串口参数：`9600 8N1`
- 同一条 485 总线上的协议：
  - Modbus RTU 寄存器读取
  - IO 板线圈写入和光电读取的原始 Modbus RTU 帧
- 当前启用轮询，除气压外：
  - 地址 `15`：温度、湿度、烟雾
  - 地址 `1`：CO
  - 地址 `2`：H2S
  - 地址 `3`：O2
  - 地址 `4`：CH4
  - 地址 `25`：光电 / IO 板
  - 地址 `210`：BMS

关键代码路径：

- [communication/serial_manager.py](../communication/serial_manager.py:160)：direct Modbus 读取与重试
- [communication/serial_manager.py](../communication/serial_manager.py:224)：IO 板和光电使用的原始命令发送
- [communication/serial_manager.py](../communication/serial_manager.py:304)：direct 请求打开串口、清缓冲、发送、读回、关闭
- [communication/serial_manager.py](../communication/serial_manager.py:351)：Modbus 回包解析与 CRC 校验
- [main.py](../main.py:520)：IO 输出动作队列
- [main.py](../main.py:798)：主传感器轮询循环
- [main.py](../main.py:850)：分组到期调度
- [main.py](../main.py:873)：分组传感器读取
- [main.py](../main.py:972)：单传感器读取重试包装
- [sensors/bms_sensors.py](../sensors/bms_sensors.py:18)：BMS 多寄存器聚合读取
- [config.py](../config.py:296)：串口默认配置
- [config.py](../config.py:357)：传感器定义
- [config.py](../config.py:503)：BMS 寄存器计划
- [config.py](../config.py:538)：轮询、超时、看门狗和分组周期配置

## 3. 时序预算

在 `9600 8N1` 下，一个字符约 `1.0417 ms`。

Modbus RTU 基础时间：

- `t1.5`：约 `1.56 ms`
- `t3.5`：约 `3.65 ms`
- 8 字节请求空中时间：约 `8.33 ms`
- 7 字节回包空中时间：约 `7.29 ms`
- 9 字节回包空中时间：约 `9.38 ms`
- 21 字节回包空中时间：约 `21.88 ms`

结论很关键：线速不是主瓶颈。一次小型成功交易通常只需要几十毫秒加设备处理时间。慢主要来自无响应超时、重试、以及多个弱设备在同一时间窗口里连续失败。

用 `rs485_timing_budget.py` 估算结果：

| 分组 | 模型 | 成功扫描估算 | 超时最坏情况 |
| --- | --- | ---: | ---: |
| 环境地址 `15` | 3 次小读，8 字节请求，7 字节回包，80 ms 间隔，1.8 s 超时 | `286.9 ms` | `5665.0 ms` |
| 气体地址 `1..4` | 4 次小读，8 字节请求，9 字节回包，80 ms 间隔，1.0 s 超时 | `390.8 ms` | `4353.3 ms` |
| 光电地址 `25` | 1 次原始读，8 字节请求，6 字节回包，50 ms 间隔，0.4 s 超时 | `64.6 ms` | `458.3 ms` |
| BMS 地址 `210` | 7 次读取，8 字节请求，21 字节回包，30 ms 间隔，1.0 s 超时 | `421.5 ms` | `7268.3 ms` |
| 全部小读近似 | 9 次交易，80 ms 间隔，1.8 s 超时 | `860.6 ms` | `16995.0 ms` |

所以现在看到的“前台像卡住”，不是因为 9600 本身扫不动，而是因为失败路径可以把一轮从 1 秒以内放大到十几秒。

## 4. 当前诊断

主瓶颈类型：调度与重试放大了物理层或从站响应不稳定。

现场和日志已经出现过这些现象：

- 地址 `15` 单测可读，但接入完整程序后间歇失败。
- 地址 `25` 的光电读和 IO 写经常 `No response`。
- 原始帧测试出现过地址错位、缺 CRC、CRC 错误、其它地址帧碎片混入。
- 单个设备单测正常，全部挂到同一 485 总线后不稳定。
- MQTT 本身能连接和发布；当上报停止时，经常是轮询循环卡在串口交易后面，而不是 MQTT 首先坏掉。

这说明：

- 问题不是简单的“总线太慢”。
- 软件当前已经比最初稳很多，但超时设备仍然会消耗大量扫描预算。
- 现场总线大概率存在拓扑、终端、电气、接地、从站响应时序或重复地址等问题。
- 地址 `25` 风险最高，因为光电读取和灯控写入共享同一个地址和物理设备。

## 5. 高概率根因排序

### 5.1 物理层或拓扑问题

CRC 错误、地址错位、帧碎片混入，是物理层或多从站响应冲突的强信号。优先怀疑：

- 总线不是单主干菊花链，而是星型或长分支。
- 终端电阻不在主干两端，或每个节点都加了终端。
- 偏置电阻过多或缺失，导致空闲电平不稳。
- A/B 极性、GND 共参考、屏蔽接地存在问题。
- 地址重复，尤其是 `15`、`25`、`210` 附近。
- 某个从站在非本地址请求后误响应或延迟响应。

现场优先测量：

- 画出真实线序、设备顺序、支线长度。
- 测 A/B 空闲差分电压。
- 用逻辑分析仪抓一次失败请求，确认是否有重叠回包或迟到回包。
- 确认主站 DE 释放后，从站回包第一个字节没有被吃掉。

### 5.2 重试放大

`read_registers_direct()` 会在持有串口锁期间完成所有重试。若某个地址超时 `1.0s`、重试 `3` 次，单个坏点就能占住总线约 3 秒。

当前风险点：

- 气体传感器如果没有单独覆盖超时和重试，会继承全局 direct 读取预算。
- BMS 一次会顺序读多个寄存器，弱响应时可能成为最长阻塞点。
- 地址 `25` 启动灯控和相机灯控失败后会进入输出重试。

### 5.3 分组仍可能同一轮集中到期

当前分组轮询已经是明显进步，但当程序刚启动或经历一次长阻塞后，`env`、`gas`、`photoelectric`、`bms`、输出队列仍可能在同一个主循环里都变成 due。

下一步应从“本轮处理所有 due 分组”升级为“每个循环只处理一个到期分组切片”，并给不同分组加相位偏移。

### 5.4 半双工方向控制和回转时间

当前通过 pyserial `RS485Settings` 启用 RS-485 模式，但默认：

- `SERIAL_RS485_DELAY_BEFORE_TX=0.0`
- `SERIAL_RS485_DELAY_BEFORE_RX=0.0`

如果主站 DE 释放过晚，可能错过从站回包第一个字节；如果释放过早，可能截断主站最后一个停止位。这一点不能靠猜，最好用逻辑分析仪或示波器确认。

### 5.5 原始命令路径对地址 `25` 仍偏乐观

`send_raw_command_with_options()` 根据命令估算回包长度，然后读固定长度。地址 `25` 弱响应时，每次 IO 输出失败都要等完整 response timeout，然后再按输出队列策略重试。

后续应增加：

- 单动作最大重试次数。
- 地址 `25` 连续失败后的长退避。
- IO 板 degraded 状态，避免坏灯控影响传感器。

## 6. 建议稳定基线

这是当前单总线现场的保守参数。目标是先止住超时风暴。

建议 `.env` 基线：

```env
SERIAL_BAUDRATE=9600
SERIAL_TIMEOUT=0.5
SERIAL_DIRECT_RETRIES=1
SERIAL_DIRECT_RESPONSE_DELAY=0.08
SERIAL_DIRECT_TIMEOUT=1.0
SERIAL_LOCK_TIMEOUT=3
SERIAL_RS485_ENABLED=true

SENSOR_READ_HARD_TIMEOUT=3
BMS_READ_HARD_TIMEOUT=12
MAX_SENSOR_ATTEMPTS=1
SENSOR_POLL_DELAY=0.3
SENSOR_RETRY_DELAY=0.2
LOOP_IDLE_DELAY=1.0
SENSOR_FAILURE_BACKOFF_SECONDS=20

ENV_SENSOR_GROUP_INTERVAL=8.0
GAS_SENSOR_GROUP_INTERVAL=5.0
BMS_GROUP_INTERVAL=10.0
PHOTOELECTRIC_POLL_INTERVAL=8.0

OUTPUT_ACTIONS_PER_CYCLE=1
OUTPUT_RETRY_DELAY=10.0
OUTPUT_RESPONSE_TIMEOUT=0.8
OUTPUT_SETTLE_DELAY=0.1
OUTPUT_STARTUP_HOLDOFF_SECONDS=15.0
OUTPUT_IDLE_GAP_SECONDS=1.0
OBSTACLE_OUTPUT_DELAY=3.0
```

原则：

- 保持 `9600`，先不要升波特率。
- 不靠增加重试掩盖不稳定，重试要少而稀疏。
- 气压继续禁用，等总线稳定后再恢复。
- 地址 `25` 输出失败应进入 IO 降级，而不是持续打总线。

## 7. 优化路线

### Phase 0：先加观测能力

每一次 485 交易都应记录结构化指标：

- 分组名
- 操作类型：`sensor_read`、`bms_read`、`photoelectric_read`、`io_write`、`mqtt_command`
- 地址
- 功能码
- 寄存器和数量
- 请求长度和预计回包长度
- 持续时间毫秒
- 结果：`ok`、`timeout`、`crc_error`、`unexpected_address`、`unexpected_function`、`missing_crc`、`exception`、`lock_timeout`
- 失败回包 hex，限制最大长度

周期发布或日志输出：

- 每地址成功/失败计数
- 每地址平均耗时和 p95 耗时
- 每地址连续失败次数
- 输出队列长度和最老动作年龄
- 主循环耗时
- 总线 degraded 状态

这样“485 慢”会变成可见的按地址预算。

### Phase 1：调度器升级为交易级队列

所有 485 操作都进入同一个调度器。

规则：

- 每个循环只执行一个交易或一个小分组切片。
- 分组有相位偏移，避免启动后同一秒集中发送。
- MQTT 下发命令也进入 485 队列，不能从回调里直接抢串口。
- 输出写入优先级低于传感器读取，安全告警输出除外。
- 失败地址进入按地址退避。

推荐相位：

- `env`：启动后 `0s`，周期 `8s`
- `gas`：启动后 `2s`，周期 `5s`
- `photoelectric`：启动后 `4s`，周期 `8s`
- `bms`：启动后 `6s`，周期 `10s`
- `outputs`：总线至少空闲 `1s` 后执行

### Phase 2：按地址熔断

每个地址维护状态机：

- `healthy`：正常周期
- `suspect`：连续 2 次失败后，周期翻倍
- `degraded`：连续 5 次失败后，低频探测并发布 stale/null 状态
- `recovered`：连续 2 次成功后恢复正常

初始策略：

| 地址 | 设备 | 正常 | 可疑 | 降级 |
| --- | --- | ---: | ---: | ---: |
| `15` | 温湿烟 | 8 s | 20 s | 60 s |
| `1..4` | 气体 | 5 s | 15 s | 45 s |
| `25` | 光电/IO | 8 s | 30 s | 120 s |
| `210` | BMS | 10 s | 30 s | 90 s |

降级时：

- 不在同一分组里立即重试。
- MQTT payload 带 `status=degraded` 或 `stale=true`。
- 其它地址继续正常轮询。

### Phase 3：降低 BMS 扫描成本

BMS 当前一次聚合读取多个寄存器，设备弱响应时会吃掉最长预算。

建议：

- 快速组每 10 秒：电压、电流、SOC、状态。
- 慢速组每 30 到 60 秒：单体电压、容量、功率。
- 未刷新字段使用 last-known-good。
- 稳定基线阶段先用 `retries=1`。

等单寄存器可靠性明确后，再考虑按相邻寄存器批量读取。

### Phase 4：治理地址 `25`

地址 `25` 同时承担光电输入和 IO 输出，应逻辑隔离。

建议：

- 光电读取：固定低频，无立即重试。
- 避障灯输出：只在状态变化时发送，延迟并合并。
- 启动灯控：可选且有最大重试次数。
- 输出连续失败 3 次后，IO 板进入 degraded，60 到 120 秒内不再重试灯控。
- 地址 `25` 的 IO 写后 1 秒内不安排光电读。

### Phase 5：现场物理层验证

软件可以提高韧性，但 CRC 和地址错位必须回到物理层解决。

现场检查表：

- 画真实总线拓扑、设备顺序、线长、支线长度。
- 确认只有主干两端有终端电阻。
- 测 A/B 空闲差分电压。
- 确认每段只有一个有效偏置网络。
- 确认设备共地或隔离方式正确。
- 检查长支线、星型接法、松动端子、屏蔽层接法。
- 抓失败波形：
  - 主站请求字节
  - DE/RE 时序
  - 从站回包起始时间
  - 是否多个从站重叠回包
  - 是否从站在主站超时后才迟到回包

### Phase 6：稳定后再提速

总线稳定后再考虑：

- 地址 `15` 的温度和湿度可尝试一次读 2 个寄存器，烟雾仍单独读。
- BMS 相邻寄存器可尝试合并。
- 在示波器或逻辑分析仪确认裕量后，再考虑 `19200`。
- 逐步缩短分组周期，并观察 p95 耗时和 CRC 计数。

## 8. 第一批落地改动

优先级最高且风险低：

1. 在 `SerialManager` 增加每次交易的耗时和结果统计。
2. 增加按地址熔断和退避。
3. 把调度器从“本轮处理所有 due 分组”改成“每轮一个 due 分组切片”，并加相位偏移。
4. 限制 IO 输出最大重试次数，并增加地址 `25` degraded 状态。
5. 把 BMS 拆成 fast/slow 两组，使用 last-known-good 缓存。
6. MQTT 下发的 485 命令也走统一调度队列。

不改代码时可先试的参数基线：

```env
SERIAL_DIRECT_RETRIES=1
MAX_SENSOR_ATTEMPTS=1
SENSOR_READ_HARD_TIMEOUT=3
BMS_READ_HARD_TIMEOUT=12
ENV_SENSOR_GROUP_INTERVAL=8
GAS_SENSOR_GROUP_INTERVAL=5
BMS_GROUP_INTERVAL=10
PHOTOELECTRIC_POLL_INTERVAL=8
OUTPUT_RETRY_DELAY=10
OUTPUT_RESPONSE_TIMEOUT=0.8
OUTPUT_IDLE_GAP_SECONDS=1.0
SENSOR_FAILURE_BACKOFF_SECONDS=20
```

如果判断正确，应看到：

- 前台不再长时间像卡死。
- 地址 `15` 或 `25` 失败时，`device/hello` 仍继续发。
- 失败日志变稀疏，不再连续刷同一地址。
- 健康的气体和 BMS 数据不会被新温湿烟或 IO 板拖停。

## 9. 验证计划

每次优化后按这个顺序验证：

1. 前台启动 `client`，确认只有一个进程。
2. 看 5 分钟日志：
   - loop heartbeat 持续
   - 无连续输出重试风暴
   - 失败地址进入退避
3. 本机订阅 MQTT：
   - `device/hello`
   - `sensors/data`
   - `sensors/bms`
4. 记录 30 分钟每地址成功/失败计数。
5. 除气压外全启用，跑 2 小时。
6. 物理层整改后，跑 24 小时。

开发板前台启动：

```sh
cd /opt/loonginx-client
/opt/python3.9/bin/python3.9 -u /opt/loonginx-client/main.py
```

常用检查：

```sh
ps -ef | grep loonginx-client
tail -f /opt/loonginx-client/sensor_gateway.log
stty -F /dev/ttyS6 -a
cat /proc/tty/driver/serial
```

## 10. 当前决策

- 保持 `9600 8N1`。
- 气压继续禁用。
- 优先做韧性和可观测性，不优先追求更快扫描。
- 地址 `15` 和 `25` 在 2 小时以上稳定运行前，视为不稳定地址。
- 一个地址不允许阻塞整个网关。

下一个工程里程碑：

- 实现交易指标、按地址退避、相位错开的交易级调度、IO 输出限次退避、BMS fast/slow 拆分。
- 然后在“除气压外全启用”的配置下跑 2 小时稳定性测试。
