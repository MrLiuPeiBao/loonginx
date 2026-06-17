# 文件讲解：`server/tests/test_ingestion_parsing.py`

## 1. 文件定位
- **角色**：测试文件
- **所在层级**：`server/tests`
- **是否建议新人优先阅读**：作为回归验证参考
- **模块文档摘要**：Unit tests for MQTT ingestion helpers.

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `from datetime import datetime, timedelta`
  - `pytest`
  - `from app.services import ingestion`
  - `from app.services.data_service import SENSOR_VALUE_FIELDS`

## 3. 核心模块与实现原理
- **函数设计**：
  - `_build_bucket(base_time: datetime, device_id: str, location: str, start_value: float)`
    - 功能：Construct a complete sensor bucket with distinct values.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：enumerate, items.append, timedelta
  - `test_parse_sensor_message_groups_by_bucket_and_coerces_values()`
    - 功能：List payloads are grouped into 2s buckets with latest timestamp retained.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_build_bucket, datetime, getattr, ingestion._parse_sensor_message, len, pytest.approx, timedelta
  - `test_parse_sensor_message_discards_incomplete_dict_payload()`
    - 功能：Dict payload missing fields is ignored to avoid partial rows.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：datetime, ingestion._parse_sensor_message, isoformat

## 4. 新人阅读建议（针对本文件）
- 先看 fixture 与断言目标，理解它覆盖的业务规则。
- 将测试名映射到被测模块，作为阅读业务代码的导航。
