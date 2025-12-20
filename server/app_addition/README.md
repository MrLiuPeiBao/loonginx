# 故障检测系统说明文档

## 概述

本模块实现了一套基于计算机视觉的输送带故障检测系统，主要用于监控输送带上的煤炭运输过程，及时发现火灾威胁和可能损坏输送带的异物。

## 系统架构

系统采用面向对象设计，包含一个抽象基类和两个具体实现类：

```
FaultDetection (基类)
├── FireAlarm (火灾检测)
└── ConveyorBeltFaultDetection (输送带故障检测)
```

## 类说明

### 1. FaultDetection - 故障检测基类

**文件**: `fault_dection.py`

**作用**: 提供所有故障检测系统的通用框架和接口定义。

**主要功能**:
- 定义抽象方法，要求子类实现具体的检测逻辑
- 管理后台线程，支持持续监控
- 提供 MQTT 告警发布接口
- 实现启动/停止控制机制
- 线程安全的状态管理

**核心方法**:
- `detect(frame)`: 抽象方法，子类需实现具体的检测算法
- `get_input_source()`: 抽象方法，返回输入源（如 RTSP URL）
- `start()`: 启动检测系统，在后台线程中运行
- `stop()`: 停止检测系统
- `publish_alarm()`: 发布告警到 MQTT 主题

**使用场景**: 作为所有故障检测系统的基类，不直接使用，而是通过子类继承使用。

---

### 2. FireAlarm - 火灾检测类

**文件**: `fire_alarm.py`

**作用**: 专门用于检测输送带上煤炭运输过程中的火灾威胁。

**检测原理**:
1. **颜色分析**: 使用 HSV 颜色空间识别火焰特征
   - 火焰通常呈现橙红色（HSV: 0-10°）和黄色（HSV: 20-30°）
   - 火焰区域具有高亮度和高饱和度特征

2. **区域统计**: 
   - 计算火焰颜色像素占比
   - 分析火焰区域的亮度值
   - 综合计算检测置信度

3. **阈值判断**: 
   - 当火焰区域占比 > 1% 且置信度达到阈值时触发告警
   - 可配置置信度阈值（默认 0.6）

**主要功能**:
- 实时处理 RTSP 视频流
- 逐帧分析检测火焰
- 自动重连机制（网络中断时自动恢复）
- 检测到火灾时自动发布告警
- 告警包含检测图像快照（Base64 编码）

**配置参数**:
- `rtsp_input`: RTSP 视频流地址
- `confidence_threshold`: 检测置信度阈值（0.0-1.0，默认 0.6）
- `detection_interval`: 最小检测间隔（秒，默认 2.0）

**告警信息**:
- 故障类型: `fire_detected`
- 置信度: 0.0-1.0 的浮点数
- 位置信息: 火焰区域的边界框坐标
- 详细信息: 火焰占比、像素数、亮度值等

**使用示例**:
```python
from app_addition.fire_alarm import FireAlarm
from app.core.config import Settings
from app.mqtt import MQTTManager

settings = Settings()
mqtt_manager = MQTTManager(settings)

fire_detector = FireAlarm(
    settings=settings,
    mqtt_manager=mqtt_manager,
    rtsp_input="rtsp://camera-ip:554/stream",
    confidence_threshold=0.6,
    detection_interval=2.0
)

fire_detector.start()  # 启动检测
# ... 系统运行中 ...
fire_detector.stop()    # 停止检测
```

---

### 3. ConveyorBeltFaultDetection - 输送带故障检测类

**文件**: `conveyor_belt_fault_dection.py`

**作用**: 检测输送带上可能撕裂或损坏输送带的异物（如石头、金属碎片等）。

**检测原理**:
1. **背景减除**: 使用 MOG2（混合高斯模型）算法
   - 自动学习背景模型
   - 识别前景移动物体
   - 过滤阴影干扰

2. **形态学处理**: 
   - 闭运算：填充物体内部空洞
   - 开运算：去除噪声点
   - 得到清晰的物体轮廓

3. **威胁评估**:
   - **面积分析**: 大物体更危险
   - **圆形度分析**: 不规则/尖锐物体更危险（圆形度 < 0.5）
   - **长宽比分析**: 细长或扁平物体可能造成撕裂
   - 综合计算威胁分数和置信度

**主要功能**:
- 实时处理 RTSP 视频流
- 自动学习背景模型（适应光照变化）
- 检测异常物体并评估威胁等级
- 检测到威胁时自动发布告警
- 告警包含检测图像快照

**配置参数**:
- `rtsp_input`: RTSP 视频流地址
- `confidence_threshold`: 检测置信度阈值（0.0-1.0，默认 0.5）
- `detection_interval`: 最小检测间隔（秒，默认 1.0）
- `size_threshold`: 最小物体尺寸（像素，默认 50）

**告警信息**:
- 故障类型: `object_on_belt`
- 置信度: 0.0-1.0 的浮点数
- 位置信息: 威胁物体的边界框坐标
- 详细信息: 
  - 威胁物体数量
  - 主要威胁物体的面积、圆形度、长宽比
  - 所有威胁物体的面积列表

**使用示例**:
```python
from app_addition.conveyor_belt_fault_dection import ConveyorBeltFaultDetection
from app.core.config import Settings
from app.mqtt import MQTTManager

settings = Settings()
mqtt_manager = MQTTManager(settings)

belt_detector = ConveyorBeltFaultDetection(
    settings=settings,
    mqtt_manager=mqtt_manager,
    rtsp_input="rtsp://camera-ip:554/stream",
    confidence_threshold=0.5,
    detection_interval=1.0,
    size_threshold=50
)

belt_detector.start()  # 启动检测
# ... 系统运行中 ...
belt_detector.stop()   # 停止检测
```

---

## 系统集成

### MQTT 告警集成

所有检测类都集成了统一的 MQTT 告警发布机制：

- **告警主题**: `sensors/alarms` (通过 `MQTT_TOPICS['alarm_broadcast']` 配置)
- **告警格式**: 符合统一的告警事件 Schema v1
- **告警内容**: 
  - 故障类型
  - 检测置信度
  - 位置信息
  - 检测系统名称
  - 详细检测数据
  - 图像快照（Base64 编码）

### 依赖要求

**必需依赖**:
- `opencv-python` (cv2): 图像处理和视频流处理
- `numpy`: 数值计算和数组操作
- `app.mqtt.MQTTManager`: MQTT 通信
- `app.core.config.Settings`: 配置管理

**可选依赖**:
- 如果缺少 OpenCV 或 NumPy，系统会优雅降级（不会崩溃，但检测功能不可用）

---

## 工作流程

### 火灾检测流程

```
1. 启动检测系统
   ↓
2. 连接 RTSP 视频流
   ↓
3. 逐帧读取视频
   ↓
4. 转换为 HSV 颜色空间
   ↓
5. 应用火焰颜色阈值（橙红/黄色）
   ↓
6. 计算火焰区域统计信息
   ↓
7. 判断置信度是否超过阈值
   ↓
8. 如果检测到火灾 → 发布告警（含图像）
   ↓
9. 继续监控下一帧
```

### 输送带故障检测流程

```
1. 启动检测系统
   ↓
2. 连接 RTSP 视频流
   ↓
3. 初始化背景减除模型（MOG2）
   ↓
4. 逐帧读取视频
   ↓
5. 应用背景减除，得到前景掩码
   ↓
6. 形态学处理，清理噪声
   ↓
7. 查找轮廓，识别物体
   ↓
8. 过滤小物体（小于 size_threshold）
   ↓
9. 分析物体特征（面积、圆形度、长宽比）
   ↓
10. 计算威胁分数和置信度
    ↓
11. 如果检测到威胁 → 发布告警（含图像）
    ↓
12. 继续监控下一帧
```

---

## 注意事项

1. **性能考虑**:
   - 检测间隔设置过小会增加 CPU 负载
   - 建议根据实际需求调整 `detection_interval`
   - 高分辨率视频流可能需要更多计算资源

2. **误报控制**:
   - 适当调整置信度阈值可以减少误报
   - 背景减除需要一定时间学习背景（初始阶段可能有误报）
   - 光照变化剧烈时可能需要重新学习背景

3. **网络稳定性**:
   - RTSP 流中断时会自动重连
   - 建议使用稳定的网络连接
   - 可以配置重连延迟时间

4. **扩展性**:
   - 可以继承 `FaultDetection` 基类创建新的检测类型
   - 只需实现 `detect()` 和 `get_input_source()` 方法
   - 可以复用基类的线程管理和告警发布功能

---

## 未来改进方向

1. **深度学习集成**: 可以使用训练好的 YOLO 模型提高检测精度
2. **多摄像头支持**: 支持同时监控多个摄像头
3. **历史记录**: 保存检测历史到数据库
4. **可视化界面**: 在 GUI 中显示实时检测结果
5. **自适应阈值**: 根据环境自动调整检测参数

