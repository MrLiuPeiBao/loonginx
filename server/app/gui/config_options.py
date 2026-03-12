"""运行期配置项定义。"""

from __future__ import annotations

from typing import Dict, List


CONFIG_SECTIONS: Dict[str, Dict[str, object]] = {
    "data_retention": {
        "title": "数据留存",
        "description": "控制历史数据保留策略与自动清理节奏。",
        "options": [
            {
                "key": "DATA_RETENTION_DAYS",
                "label": "保留天数",
                "type": "int",
                "default": 0,
                "effect": "热更新",
                "hint": "0 表示不按天数清理。",
            },
            {
                "key": "DATA_RETENTION_MAX_GB",
                "label": "最大占用(GB)",
                "type": "float",
                "default": 0.0,
                "effect": "热更新",
                "hint": "0 表示不按容量清理。",
            },
            {
                "key": "DATA_RETENTION_CHECK_INTERVAL_SECONDS",
                "label": "检查间隔(秒)",
                "type": "int",
                "default": 3600,
                "effect": "热更新",
                "hint": "自动清理扫描的间隔。",
            },
            {
                "key": "DATA_RETENTION_TABLES",
                "label": "清理表(逗号分隔)",
                "type": "list",
                "default": "",
                "effect": "热更新",
                "hint": "留空则使用默认表。",
            },
        ],
    },
    "command": {
        "title": "命令策略",
        "description": "控制指令执行与超时策略。",
        "options": [
            {
                "key": "COMMAND_TIMEOUT_SECONDS",
                "label": "命令超时(秒)",
                "type": "int",
                "default": 15,
                "effect": "热更新",
                "hint": "超过该时间判定为超时。",
            },
        ],
    },
}
