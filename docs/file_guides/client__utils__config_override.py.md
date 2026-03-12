# 文件讲解：`client/utils/config_override.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/utils`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：运行期配置覆盖加载与合并。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `json`
  - `from pathlib import Path`
  - `from typing import Dict, Optional`

## 3. 核心模块与实现原理
- **关键常量**：`CONFIG_UPDATED_AT_KEY, CONFIG_VERSION_KEY, DEFAULT_OVERRIDE_PATH, HOT_UPDATE_KEYS`
- **函数设计**：
  - `load_overrides(path: Optional[Path] = None)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：isinstance, json.loads, target.exists, target.read_text
  - `save_overrides(overrides: Dict[str, object], path: Optional[Path] = None)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：json.dumps, target.write_text
  - `get_config_version(overrides: Optional[Dict[str, object]] = None)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：data.get, int, load_overrides
  - `get_config_updated_at(overrides: Optional[Dict[str, object]] = None)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：data.get, float, load_overrides
  - `merge_overrides(base: Dict[str, object], overrides: Optional[Dict[str, object]])`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：dict, merged.update
  - `split_hot_and_restart(overrides: Optional[Dict[str, object]])`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：items

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
