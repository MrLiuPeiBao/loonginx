# 文件讲解：`client/communication/mqtt_client.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/communication`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `json`
  - `logging`
  - `time`
  - `from collections import deque`
  - `from typing import Any, Callable, Deque, Dict, Optional, Tuple`
  - `threading`
  - `paho.mqtt.client as mqtt`

## 3. 核心模块与实现原理
- **类设计**：
  - `MQTTClient`（继承：无）
    - 作用：Thin wrapper around paho-mqtt with callback registration.
    - 方法数量：17
    - `__init__(self, config: dict)`
      - 关键调用链：bool, config.get, deque, dict, int, lower
    - `_on_connect(self, client, userdata, flags, rc)`
      - 关键调用链：client.subscribe, logging.error, logging.info, self._drain_offline_queue
    - `_on_message(self, client, userdata, msg)`
      - 关键调用链：callback, logging.warning, self.message_callbacks.get
    - `_on_disconnect(self, client, userdata, rc)`
      - 关键调用链：logging.info
    - `connect(self)`
      - 职责：Connect to the broker and start the network loop.
      - 关键调用链：logging.error, self.client.connect, self.client.loop_start, self.config.get, time.monotonic
    - `_format_payload_for_log(self, payload: Any)`
      - 关键调用链：isinstance, json.dumps, payload.hex, str
    - `publish(self, topic: str, payload: Any)`
      - 职责：Publish a message. Non-bytes payloads are JSON serialised.
      - 关键调用链：isinstance, json.dumps, len, logging.error, logging.info, logging.warning
    - `_resolve_qos(self, topic: str)`
      - 关键调用链：int
    - ... 其余 9 个方法建议在 IDE 中按调用层级继续追踪

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
