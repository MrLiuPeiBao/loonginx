# 文件讲解：`server/app/schemas/__init__.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/schemas`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：Pydantic/SQLModel 架构声明。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from .sensor import SensorDataCreate, SensorDataRead`
  - `from .bms import BMSDataCreate, BMSDataRead`
  - `from .rfid import RFIDDataCreate, RFIDDataRead`
  - `from .config import SensorConfigCreate, SensorConfigRead`
  - `from .command import CommandLogRead, CommandRequest`
  - `from .alarm import AlarmRecordRead`
  - `from .image import ImageDataCreate, ImageDataRead`
  - `from .audio import AudioDataCreate, AudioDataRead`
  - `from .metal import MetalAnomalyCreate, MetalAnomalyRead`

## 3. 核心模块与实现原理
- 本文件无类/函数定义，多用于包初始化、常量导出或脚本入口。

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
