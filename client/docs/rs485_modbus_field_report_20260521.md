# RS-485 / Modbus RTU 现场排查报告

日期：2026-05-21  
对象：开发板 `192.168.0.102`，板端目录 `/opt/loonginx-client`，485 串口 `/dev/ttyS6`

## 结论摘要

本次实测不支持“多个传感器同时回复导致冲突”作为主因，也不支持“Python minimalmodbus 库或自编译 Python 环境”作为主因。

关键证据是：停止主程序、独占 `/dev/ttyS6` 后，Python raw、minimalmodbus、C raw 都复现了稳定的 50% 成功率；单独查询 `photo` 一个从站也表现为一次成功、一次超时的交替模式。失败类型几乎都是 `timeout`，没有观察到 CRC 错、错地址、错功能码或残帧。

最高概率方向是 `/dev/ttyS6` 对应 UART/RS485 转向链路、485 收发器控制、串口驱动状态或板端硬件层存在“隔次事务无回复”的状态问题。软件重试可以把最终成功率补到 100%，但只是掩盖第一拍失败，并会直接牺牲总线吞吐。

## 实验环境

| 项目 | 实测值 |
| --- | --- |
| 开发板 | `192.168.0.102` |
| 架构 | `loongarch64` |
| OS | `Linux-5.10.97.lsgd-loongarch64-with-glibc2.28` |
| Python | `/opt/python3.9/bin/python3.9`，`3.9.19` |
| pyserial | `3.5` |
| minimalmodbus | `2.1.1` |
| 485 串口 | `/dev/ttyS6` |
| 串口参数 | `9600 8N1` |
| 业务配置 | `.env` 中 `SERIAL_RS485_ENABLED=true` |
| C raw | WSL 使用 `loongarch64-linux-gnu-gcc-14 -O2 -static` 编译，板端运行 `/tmp/rs485_modbus_probe_c` |

实验前执行 `scripts/release_ttys6.py`，结果：

```json
{"serial_port":"/dev/ttyS6","tty_holders_before":[],"tty_holder_actions":[],"tty_holders_after":[]}
```

实验后已重新启动业务：

```text
/bin/sh /opt/loonginx-client/run_client_forever.sh
/opt/python3.9/bin/python3.9 -u /opt/loonginx-client/main.py
```

## 现场脚本

本次新增本地脚本：

| 脚本 | 用途 |
| --- | --- |
| `scripts/rs485_modbus_probe.py` | Python raw / minimalmodbus / listen JSONL 探针 |
| `scripts/rs485_modbus_probe_c.c` | C raw termios 探针源码 |
| `/tmp/rs485_modbus_probe_c` | C raw 静态二进制，已上传到开发板；本地二进制为临时编译产物，未保留 |

所有实验日志保存在板端：

```text
/opt/loonginx-client/logs/rs485_experiments/*.jsonl
```

## 实验数据

### P0 总线静默监听

主程序停止后监听 10 秒：

| 日志 | 结果 |
| --- | --- |
| `20260521_161023_p0_listen.jsonl` | `chunks=0`，`bytes=0` |

判断：没有观察到其它主站、迟到帧或持续噪声主动占线。

### Python raw 基线

参数：`/dev/ttyS6`，`9600 8N1`，open/close，每次 1 个请求，无额外重试，`gap=200ms`。

| 实验 | 样本 | 成功 | 成功率 | 主要错误 |
| --- | ---: | ---: | ---: | --- |
| env：地址 15，温/湿/烟 | 60 | 30 | 50% | `timeout=30` |
| gas：地址 1/2/3/4 | 40 | 20 | 50% | `timeout=20` |
| BMS fast：地址 210 | 40 | 20 | 50% | `timeout=20` |
| photo：地址 25，单从站重复查询 | 30 | 15 | 50% | `timeout=15` |

分项规律：

| 实验 | 分项结果 |
| --- | --- |
| env | `env-temp/env-humi/env-smoke` 均为 10/20 成功 |
| gas | `co=0/10`，`h2s=10/10`，`o2=0/10`，`ch4=10/10` |
| BMS fast | `voltage=0/10`，`current=10/10`，`soc=0/10`，`status=10/10` |
| photo | 奇偶事务交替：一次超时、一次成功；成功回包为 `19 02 01 00 a7 28` |

判断：失败不是随机错帧，而是事务序号相关的稳定交替。

### minimalmodbus 对照

参数：同样 `9600 8N1`，open/close，`gap=200ms`。

| 实验 | 样本 | 成功 | 成功率 | 主要错误 |
| --- | ---: | ---: | ---: | --- |
| minimal env | 30 | 15 | 50% | `NoResponseError` / timeout |
| minimal gas | 20 | 10 | 50% | timeout |

判断：minimalmodbus 没有比 Python raw 更差；成功/失败落在哪个地址上会随起始相位变化，但整体仍是 50%。

### C raw 对照

参数：C `termios + write + tcdrain + poll/read deadline + Modbus CRC16`，`gap=200ms`，open/close。

| 实验 | 样本 | 成功 | 成功率 |
| --- | ---: | ---: | ---: |
| C raw photo | 20 | 10 | 50% |
| C raw env | 30 | 15 | 50% |
| C raw gas | 20 | 10 | 50% |
| C raw BMS fast | 20 | 10 | 50% |

判断：C raw 与 Python raw 一致，反证“换 C 库即可解决”。

### gap 扫描

对象：`photo` 单从站，C raw。

| gap | 样本 | 成功 | 成功率 |
| ---: | ---: | ---: | ---: |
| 20ms | 20 | 10 | 50% |
| 80ms | 20 | 10 | 50% |
| 500ms | 20 | 10 | 50% |

判断：把请求间隔拉大没有恢复成功率，不支持“轮询太快导致回复冲突”。

### 重试验证

对象：`photo` 单从站，Python raw，`extra_retry=1`。

| 样本 | 最终成功 | 最终成功率 | 首次成功 | 首次成功率 |
| ---: | ---: | ---: | ---: | ---: |
| 20 | 20 | 100% | 1 | 5% |

典型样本：

```json
{"status":"ok","attempts":2,"first_attempt_status":"timeout","attempt_statuses":["timeout","ok"],"ok_attempt":2}
```

判断：重试能补偿交替失败，但不是根因修复。

### RS485 mode 清除对照

执行 `ser.rs485_mode = None` 后运行 C raw `photo`，无 `--rs485`：

| 样本 | 成功 | 成功率 |
| ---: | ---: | ---: |
| 20 | 10 | 50% |

判断：不是 pyserial RS485 mode 残留导致的单一现象。

### keep-open 观察

Python/C keep-open 模式在首个超时后出现卡住，需要终止。由于生产 direct 路径当前是 open/close 模式，本报告不把 keep-open 作为优化建议；该现象仅作为“串口驱动/设备状态恢复敏感”的补充证据。

## 对三个怀疑点的判定

### 怀疑点 1：轮询算法或半双工同步导致多个从站回复冲突

结论：不支持作为主因。

证据：

- 主程序停止后静默监听 10 秒，`bytes=0`。
- 单独重复查询一个从站 `photo`，仍稳定 50%。
- gap 从 `20ms` 增加到 `500ms`，成功率仍为 50%。
- 错误类型是 `timeout`，没有 CRC 错、错地址、错功能码或混帧。
- 代码层面主轮询 group 内是顺序读取，并且 direct/minimal/raw command 共用同一把串口锁。

保留风险：如果现场接线存在终端/偏置/共地问题，仍可能造成“无响应”，但本次数据不像多从站抢答。

### 怀疑点 2：Python Modbus 库不适合现有工程环境，换 C 库能解决

结论：不支持。

证据：

- Python raw、minimalmodbus、C raw 均为 50%。
- C raw 对单从站 `photo` 也稳定交替成功/超时。
- minimalmodbus 没有显著低于 raw；它只是受交替相位影响，成功地址集合不同。

### 怀疑点 3：自编译 Python 或库文件导致 Modbus 效果异常

结论：不支持作为主因。

证据：

- `/opt/python3.9/bin/python3.9` 下 Python raw 与 C raw 结果一致。
- C raw 不依赖 Python、pyserial、minimalmodbus，仍复现 50%。

## 当前最高概率根因

优先怀疑顺序：

1. `/dev/ttyS6` 对应 UART/RS485 驱动或收发器方向控制存在隔次事务状态问题。
2. 485 转换电路的 DE/RE 控制、自动转向、RTS 极性或硬件使能存在边沿/状态翻转问题。
3. 串口 open/close 后收发器或驱动状态没有稳定复位，导致一次请求实际未被从站收到。
4. 物理层终端/偏置/共地仍需复查，但本次未观察到 CRC 错或残帧，优先级低于方向/驱动链路。

## 短期止血建议

1. 给所有读请求增加“立即一次重试”，并记录 `first_attempt_success_rate`，不要只看最终成功率。
2. `photo` 当前 `max_attempts=1`，可先改为 2；gas/BMS 已有重试但应区分首包成功和最终成功。
3. 避免改成 keep-open 模式作为快速修复，本次 keep-open 在失败后卡住。
4. 降低业务轮询压力只作为保护措施；本次 gap 扫描显示它不能修复 50% 根因。

## 已落地的软件调整

- 环境传感器 `temperature/humidity/smoke` 的 `direct_retries` 调整为 `2`，在单次业务读取内完成第二拍补问。
- 光电 `photoelectric` 的 `max_attempts` 调整为 `2`。
- `SENSOR_RETRY_DELAY` 默认调整为 `0.05s`，用于快速补问第二拍。
- 轮询日志记录 `Sensor read recovered ... attempt=2/2`，串口 direct 事务指标记录 `first_attempt_status`、`ok_attempt` 和 `attempt_statuses`，避免把重试后的最终成功误判为链路健康。
- 原始命令发送后显式关闭串口，避免光电 raw command 路径长期保持 `/dev/ttyS6` 打开。

## 后续硬件/驱动验证

1. 用示波器或逻辑分析仪同时看 `TXD/RXD/DE/RE/A/B`，确认失败事务是否真的发到了 485 总线。
2. 若失败事务 TXD 有数据但 A/B 无有效差分，查 DE/RE、RTS 极性和 485 收发器。
3. 若 A/B 有请求但从站无回复，查从站地址/协议/供电/接线；但需解释为什么严格隔次失败。
4. 若从站有回复但板端 RXD 没收到，查 RX 使能释放时序、驱动 RS485 mode、串口复用和硬件流控。
5. 在另一条 UART 或 USB-RS485 适配器上复跑同一 C raw `photo` 实验；如果成功率恢复到接近 100%，即可把问题定位到当前 `/dev/ttyS6` 链路。

## 复跑命令

```sh
cd /opt/loonginx-client
/opt/python3.9/bin/python3.9 scripts/release_ttys6.py --device /dev/ttyS6

/opt/python3.9/bin/python3.9 /tmp/rs485_modbus_probe.py \
  --tool pyraw --mode single --target photo --samples 30 \
  --gap-ms 200 --extra-retry 0 --rs485 --port /dev/ttyS6 --baud 9600

/tmp/rs485_modbus_probe_c \
  --target photo --samples 20 --gap-ms 200 --rs485

/opt/python3.9/bin/python3.9 /tmp/rs485_modbus_probe.py \
  --tool minimal --mode batch --scenario env --cycles 10 \
  --gap-ms 200 --rs485 --port /dev/ttyS6 --baud 9600
```
