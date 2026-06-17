# loonginx 代码库新人导读（自动化初稿）

## 一、项目整体结构
- 项目由 `client/`（下位机采集与上报）和 `server/`（上位机接收、入库、告警、API/GUI）两大子系统组成。
- `client` 重点是设备侧采集、MQTT 上报、PLC/RFID 通信；`server` 重点是消息消费、数据服务、运行期配置与可视化。
- 当前扫描到 Python 程序文件 **102** 个（client 29 / server 73）。

## 二、建议优先掌握的关键内容
1. **端到端链路**：传感器/PLC/RFID -> client MQTT -> server ingestion -> DB/API/告警。
2. **配置体系**：`client/config.py` 与 `server/app/core/config.py`，以及运行期配置同步机制。
3. **可靠性机制**：离线队列、降频策略、重连与后台守护、数据保留清理。
4. **测试策略**：先从 `tests/` 反推业务规则，再深入服务实现。

## 三、每个程序文件说明文档
- 文档目录：`docs/file_guides/`。
- 命名规则：将源码相对路径中的 `/` 转为 `__`，例如 `server/app/services/data_service.py` 对应 `docs/file_guides/server__app__services__data_service.py.md`。

## 四、后续学习建议（3 周路线）
- **第 1 周：系统走读**：只看入口、配置、主流程和 5~8 个关键测试，画出时序图。
- **第 2 周：稳定性专题**：重点读 MQTT/DB/串口重试与降级逻辑，补齐本地故障演练脚本。
- **第 3 周：业务扩展**：尝试新增一种传感器或告警规则，并补充对应测试。

## 五、如何使用这些文档
- 每个文件文档包含：定位、依赖、类函数拆解、调用关系与阅读建议。
- 建议配合 IDE 的 “Find Usages / Call Hierarchy” 功能，继续从自动摘要深入到源码细节。
