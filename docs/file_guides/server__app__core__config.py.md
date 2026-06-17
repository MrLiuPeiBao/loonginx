# 文件讲解：`server/app/core/config.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/core`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：项目配置与环境加载支持。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `json`
  - `from functools import lru_cache`
  - `from pathlib import Path`
  - `from typing import Optional, Union`
  - `from dotenv import load_dotenv`
  - `from sqlalchemy.engine import URL`

## 3. 核心模块与实现原理
- **关键常量**：`RUNTIME_HOT_UPDATE_KEYS, RUNTIME_HOT_UPDATE_PREFIXES`
- **类设计**：
  - `Settings`（继承：BaseSettings）
    - 作用：全局配置。
    - 方法数量：5
    - `parse_cors_allow_origins(cls, value: object)`
      - 职责：解析 CORS 允许来源配置。
      - 关键调用链：isinstance, item.strip, json.loads, str, strip, stripped.split
    - `normalize_run_mode(cls, value: object)`
      - 关键调用链：lower, str, strip, validator
    - `normalize_media_storage_mode(cls, value: object)`
      - 关键调用链：lower, str, strip, validator
    - `parse_data_retention_tables(cls, value: object)`
      - 关键调用链：isinstance, item.strip, stripped.split, validator, value.strip
    - `mysql_url(self)`
      - 职责：构建 SQLAlchemy URL。
      - 关键调用链：URL.create
- **函数设计**：
  - `load_env(env_file: Optional[Union[str, Path]] = None)`
    - 功能：加载 .env 配置文件到环境变量。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：Path, candidate.is_file, load_dotenv, path.is_file
  - `is_hot_update_key(key: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：str, upper, upper.startswith
  - `merge_runtime_overrides(base: dict, overrides: dict | None)`
    - 功能：合并运行期覆盖配置。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：dict, merged.update
  - `split_runtime_override_keys(overrides: dict | None)`
    - 功能：区分可热更与需重启的配置键。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：is_hot_update_key, keys, list
  - `get_settings()`
    - 功能：返回 Settings 单例。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：Settings, load_env, lru_cache

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
