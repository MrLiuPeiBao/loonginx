"""Configuration for the sensor gateway."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv:
    load_dotenv()
else:
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip().strip('"').strip("'")
        except OSError:
            pass

import utils.config_override as config_override_module


def _get_env(name: str, default: object) -> object:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _get_int_env(name: str, default: int) -> int:
    value = _get_env(name, None)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_float_env(name: str, default: float) -> float:
    value = _get_env(name, None)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_bool_env(name: str, default: bool) -> bool:
    value = _get_env(name, None)
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return default


def _get_str_env(name: str, default: str) -> str:
    value = _get_env(name, None)
    if value is None:
        return default
    return str(value)


def _get_json_or_kv_env(name: str, default: dict) -> dict:
    value = _get_env(name, None)
    if value is None:
        return default
    raw = str(value).strip()
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return {str(k): int(v) for k, v in parsed.items()}

    result = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        key, val = item.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        try:
            result[key] = int(val)
        except Exception:
            continue
    return result if result else default


def _coerce_str_list(value: object) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            value = parsed
        else:
            value = [part.strip() for part in raw.split(",")]

    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            text = str(item).strip()
            if text:
                result.append(text)
        return result

    return []


def _get_thresholds_env(name: str, default: dict) -> dict:
    value = _get_env(name, None)
    if value is None:
        return default
    raw = str(value).strip()
    if not raw:
        return default

    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None

    result = {}
    if isinstance(parsed, dict):
        for sensor_type, bounds in parsed.items():
            if not isinstance(bounds, dict):
                continue
            result[str(sensor_type)] = {
                "min": _optional_float(bounds.get("min")),
                "max": _optional_float(bounds.get("max")),
            }
        return result if result else default

    for item in raw.split(","):
        if "=" not in item:
            continue
        sensor_type, bounds_text = item.split("=", 1)
        parts = [part.strip() for part in bounds_text.split(":")]
        if len(parts) != 2:
            continue
        result[sensor_type.strip()] = {
            "min": _optional_float(parts[0]),
            "max": _optional_float(parts[1]),
        }
    return result if result else default


def _coerce_thresholds_value(value: object, default: dict) -> dict:
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return default
        try:
            value = json.loads(raw)
        except Exception:
            return default

    if not isinstance(value, dict):
        return default

    result = {}
    for sensor_type, bounds in value.items():
        if not isinstance(bounds, dict):
            continue
        result[str(sensor_type)] = {
            "min": _optional_float(bounds.get("min")),
            "max": _optional_float(bounds.get("max")),
        }
    return result if result else default


def _optional_float(value: object) -> object:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _camera_host_from_target(target: str) -> str:
    text = str(target or "").strip()
    if not text:
        return ""
    if "://" in text:
        parsed = urlparse(text)
        return parsed.hostname or ""
    return text.split("/", 1)[0].split(":", 1)[0]


def _cast_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return default


def _apply_override(key: str, current: object, caster) -> object:
    if key not in _RUNTIME_OVERRIDES:
        return current
    value = _RUNTIME_OVERRIDES.get(key)
    try:
        if caster is _cast_bool:
            return _cast_bool(value, bool(current))
        return caster(value)
    except (TypeError, ValueError):
        return current


MQTT_CONFIG = {
    "broker": _get_env("MQTT_BROKER", "192.168.0.100"),
    "port": _get_int_env("MQTT_PORT", 1883),
    "client_id": _get_env("MQTT_CLIENT_ID", "loongson_sensor_gateway"),
    "username": _get_env("MQTT_USERNAME", None),
    "password": _get_env("MQTT_PASSWORD", None),
    "keepalive": _get_int_env("MQTT_KEEPALIVE", 60),
    "connect_timeout": _get_float_env("MQTT_CONNECT_TIMEOUT", 12.0),
    "publish_qos": _get_int_env("MQTT_PUBLISH_QOS", 0),
    "offline_queue_enabled": _get_bool_env("MQTT_OFFLINE_QUEUE_ENABLED", False),
    "offline_queue_max_items": _get_int_env("MQTT_OFFLINE_QUEUE_MAX_ITEMS", 500),
    "offline_queue_max_bytes": _get_int_env("MQTT_OFFLINE_QUEUE_MAX_BYTES", 2_000_000),
    "topic_qos_map": _get_json_or_kv_env("MQTT_TOPIC_QOS_MAP", {}),
    "offline_policy": _get_str_env("MQTT_OFFLINE_POLICY", "queue"),
}

_RUNTIME_OVERRIDES = config_override_module.load_overrides()
if "pytest" in sys.modules and os.getenv("ENABLED_SENSOR_TYPES"):
    _RUNTIME_OVERRIDES = dict(_RUNTIME_OVERRIDES)
    _RUNTIME_OVERRIDES.pop("ENABLED_SENSOR_TYPES", None)
if _RUNTIME_OVERRIDES:
    mqtt_override_map = {
        "MQTT_BROKER": ("broker", str),
        "MQTT_PORT": ("port", int),
        "MQTT_CLIENT_ID": ("client_id", str),
        "MQTT_USERNAME": ("username", str),
        "MQTT_PASSWORD": ("password", str),
        "MQTT_KEEPALIVE": ("keepalive", int),
        "MQTT_CONNECT_TIMEOUT": ("connect_timeout", float),
        "MQTT_PUBLISH_QOS": ("publish_qos", int),
        "MQTT_OFFLINE_QUEUE_ENABLED": ("offline_queue_enabled", _cast_bool),
        "MQTT_OFFLINE_QUEUE_MAX_ITEMS": ("offline_queue_max_items", int),
        "MQTT_OFFLINE_QUEUE_MAX_BYTES": ("offline_queue_max_bytes", int),
        "MQTT_OFFLINE_POLICY": ("offline_policy", str),
    }
    for key, (field, caster) in mqtt_override_map.items():
        MQTT_CONFIG[field] = _apply_override(key, MQTT_CONFIG[field], caster)

MQTT_TOPIC_QOS_MAP = MQTT_CONFIG["topic_qos_map"]
MQTT_OFFLINE_POLICY = MQTT_CONFIG["offline_policy"]

MQTT_TOPICS = {
    "sensor_data": "sensors/data",
    "bms_data": "sensors/bms",
    "rfid_data": "sensors/rfid",
    "alarm_broadcast": "sensors/alarms",
    "command_request": "sensors/command/request",
    "command_response": "sensors/command/response",
    "config_update": "config/update",
    "config_ack": "config/ack",
    "device_hello": "device/hello",
}

SERIAL_DIRECT_RETRIES = _get_int_env("SERIAL_DIRECT_RETRIES", 3)
SERIAL_DIRECT_RESPONSE_DELAY = _get_float_env("SERIAL_DIRECT_RESPONSE_DELAY", 0.02)
SERIAL_DIRECT_TIMEOUT = _get_float_env("SERIAL_DIRECT_TIMEOUT", 1.0)
SERIAL_LOCK_TIMEOUT = max(_get_float_env("SERIAL_LOCK_TIMEOUT", 3.0), 8.0)
SERIAL_RS485_ENABLED = _get_bool_env("SERIAL_RS485_ENABLED", False)
SERIAL_RS485_RTS_LEVEL_FOR_TX = _get_bool_env("SERIAL_RS485_RTS_LEVEL_FOR_TX", True)
SERIAL_RS485_RTS_LEVEL_FOR_RX = _get_bool_env("SERIAL_RS485_RTS_LEVEL_FOR_RX", False)
SERIAL_RS485_LOOPBACK = _get_bool_env("SERIAL_RS485_LOOPBACK", False)
SERIAL_RS485_DELAY_BEFORE_TX = _get_float_env("SERIAL_RS485_DELAY_BEFORE_TX", 0.0)
SERIAL_RS485_DELAY_BEFORE_RX = _get_float_env("SERIAL_RS485_DELAY_BEFORE_RX", 0.0)

SERIAL_CONFIG = {
    "port": _get_env("SERIAL_PORT", "/dev/ttyS6"),
    "baudrate": _get_int_env("SERIAL_BAUDRATE", 9600),
    "timeout": _get_float_env("SERIAL_TIMEOUT", 0.5),
    "direct_retries": SERIAL_DIRECT_RETRIES,
    "direct_response_delay": SERIAL_DIRECT_RESPONSE_DELAY,
    "direct_timeout": SERIAL_DIRECT_TIMEOUT,
    "lock_timeout": SERIAL_LOCK_TIMEOUT,
    "rs485_enabled": SERIAL_RS485_ENABLED,
    "rs485_rts_level_for_tx": SERIAL_RS485_RTS_LEVEL_FOR_TX,
    "rs485_rts_level_for_rx": SERIAL_RS485_RTS_LEVEL_FOR_RX,
    "rs485_loopback": SERIAL_RS485_LOOPBACK,
    "rs485_delay_before_tx": SERIAL_RS485_DELAY_BEFORE_TX,
    "rs485_delay_before_rx": SERIAL_RS485_DELAY_BEFORE_RX,
}

RFID_SERIAL_CONFIG = {
    "port": _get_env("RFID_SERIAL_PORT", "/dev/ttyS5"),
    "baudrate": _get_int_env("RFID_SERIAL_BAUDRATE", 9600),
    "duplicate_suppress_seconds": _get_float_env("RFID_DUPLICATE_SUPPRESS_SECONDS", 1.0),
}

CAMERA_TARGET = _get_str_env("CAMERA_TARGET", _get_str_env("CAMERA_IP", "192.168.0.101"))
CAMERA_HOST = _camera_host_from_target(CAMERA_TARGET)
CAMERA_PORT = _get_int_env("CAMERA_PORT", 554)
CAMERA_TIMEOUT = _get_float_env("CAMERA_TIMEOUT", 1.0)

IO_BOARD_CONFIG = {
    "enabled": _get_bool_env("IO_BOARD_ENABLED", True),
    "address": _get_int_env("IO_BOARD_ADDRESS", 25),
}

LOCAL_SENSOR_THRESHOLDS = _get_thresholds_env(
    "LOCAL_SENSOR_THRESHOLDS",
    {
        "temperature": {"min": 10.0, "max": 35.0},
        "humidity": {"min": 20.0, "max": 80.0},
        "pressure": {"min": 90.0, "max": 110.0},
        "smoke": {"min": 0.0, "max": 5.0},
        "co": {"min": 0.0, "max": 35.0},
        "o2": {"min": 19.5, "max": 23.5},
        "h2s": {"min": 0.0, "max": 10.0},
        "ch4": {"min": 0.0, "max": 1.0},
    },
)
LOCAL_SENSOR_THRESHOLDS = _coerce_thresholds_value(
    _RUNTIME_OVERRIDES.get("LOCAL_SENSOR_THRESHOLDS"),
    LOCAL_SENSOR_THRESHOLDS,
)
ALARM_OUTPUT_HOLD_SECONDS = _get_float_env("ALARM_OUTPUT_HOLD_SECONDS", 30.0)

SENSOR_CONFIGS = {
    # Original split temperature/humidity sensors, kept for rollback:
    # "temperature": {
    #     "address": 30,
    #     "register_addr": 16,
    #     "registers": 2,
    #     "parse_type": "dcba",
    #     "function_code": 3,
    # },
    # "humidity": {
    #     "address": 31,
    #     "register_addr": 16,
    #     "registers": 2,
    #     "parse_type": "dcba",
    #     "function_code": 3,
    # },
    # Original standalone smoke sensor, kept for rollback:
    # "smoke": {
    #     "address": 5,
    #     "register_addr": 0x00,
    #     "registers": 2,
    #     "parse_type": "smoke",
    #     "function_code": 3,
    # },
    # Shared block-read mode for Shangluo environment sensor, kept for rollback:
    # "temperature": {
    #     "address": 15,
    #     "register_addr": 1,
    #     "registers": 1,
    #     "parse_type": "shangluo_temperature",
    #     "function_code": 3,
    #     "read_mode": "shared_direct",
    #     "direct_retries": 1,
    #     "direct_timeout": 1.0,
    #     "shared_group": "env15",
    #     "shared_register_addr": 1,
    #     "shared_registers": 11,
    #     "shared_index": 0,
    # },
    # "humidity": {
    #     "address": 15,
    #     "register_addr": 2,
    #     "registers": 1,
    #     "parse_type": "shangluo_humidity",
    #     "function_code": 3,
    #     "read_mode": "shared_direct",
    #     "direct_retries": 1,
    #     "direct_timeout": 1.0,
    #     "shared_group": "env15",
    #     "shared_register_addr": 1,
    #     "shared_registers": 11,
    #     "shared_index": 1,
    # },
    # "smoke": {
    #     "address": 15,
    #     "register_addr": 11,
    #     "registers": 1,
    #     "parse_type": "shangluo_smoke",
    #     "function_code": 3,
    #     "read_mode": "shared_direct",
    #     "direct_retries": 1,
    #     "direct_timeout": 1.0,
    #     "shared_group": "env15",
    #     "shared_register_addr": 1,
    #     "shared_registers": 11,
    #     "shared_index": 10,
    # },
    "temperature": {
        "address": 15,
        "register_addr": 1,
        "registers": 1,
        "parse_type": "shangluo_temperature",
        "function_code": 3,
        "read_mode": "direct",
        "direct_retries": 2,
        "direct_response_delay": 0.08,
        "direct_timeout": 1.8,
        "max_attempts": 1,
    },
    "humidity": {
        "address": 15,
        "register_addr": 2,
        "registers": 1,
        "parse_type": "shangluo_humidity",
        "function_code": 3,
        "read_mode": "direct",
        "direct_retries": 2,
        "direct_response_delay": 0.08,
        "direct_timeout": 1.8,
        "max_attempts": 1,
    },
    "pressure": {
        "address": 32,
        "register_addr": 16,
        "registers": 2,
        "parse_type": "dcba",
        "function_code": 3,
    },
    "co": {
        "address": 1,
        "register_addr": 0x65,
        "registers": 2,
        "parse_type": "raw",
        "function_code": 3,
        "direct_retries": 2,
        "direct_response_delay": 0.08,
        "direct_timeout": 1.2,
        "max_attempts": 1,
    },
    "h2s": {
        "address": 2,
        "register_addr": 0x65,
        "registers": 2,
        "parse_type": "raw",
        "function_code": 3,
        "direct_retries": 2,
        "direct_response_delay": 0.08,
        "direct_timeout": 1.2,
        "max_attempts": 1,
    },
    "o2": {
        "address": 3,
        "register_addr": 0x65,
        "registers": 2,
        "parse_type": "o2",
        "function_code": 3,
        "direct_retries": 2,
        "direct_response_delay": 0.08,
        "direct_timeout": 1.2,
        "max_attempts": 1,
    },
    "ch4": {
        "address": 4,
        "register_addr": 0x65,
        "registers": 2,
        "parse_type": "raw",
        "function_code": 3,
        "direct_retries": 2,
        "direct_response_delay": 0.08,
        "direct_timeout": 1.2,
        "max_attempts": 1,
    },
    "smoke": {
        "address": 15,
        "register_addr": 11,
        "registers": 1,
        "parse_type": "shangluo_smoke",
        "function_code": 3,
        "read_mode": "direct",
        "direct_retries": 2,
        "direct_response_delay": 0.08,
        "direct_timeout": 1.8,
        "max_attempts": 1,
    },
    "photoelectric": {
        "address": IO_BOARD_CONFIG["address"],
        "command_hex": "19 02 00 00 00 02 FA 13",
        "poll_interval": 2.0,
        "response_timeout": 0.25,
        "max_attempts": 2,
    },
}

BMS_CONFIG = {
    "address": 210,
    "function_code": 3,
    "max_retries": 2,
    "response_delay": 0.05,
    "response_timeout": 1.0,
    "fast_keys": ("voltage", "current", "soc", "status"),
    "slow_keys": ("capacity", "power", "cell_voltages"),
    "registers": {
        "voltage": {"addr": 0x28, "multiplier": 0.1},
        "soc": {"addr": 0x2A, "multiplier": 0.001},
        "status": {"addr": 0x2F, "multiplier": 1},
        "capacity": {"addr": 0x30, "multiplier": 0.1},
        "power": {"addr": 0x39, "multiplier": 1},
        "cell_voltages": {
            "addr": 0x00,
            "registers": 8,
            "multiplier": 0.001,
            "precision": 3,
        },
        "current": {
            "addr": 0x29,
            "multiplier": 0.1,
            "offset": -30000,
            "precision": 1,
        },
    },
}

MESSAGE_SCHEMA_VERSION = _get_int_env("MESSAGE_SCHEMA_VERSION", 1)
SENSOR_POLL_DELAY = _get_float_env("SENSOR_POLL_DELAY", 0.2)
SENSOR_RETRY_DELAY = _get_float_env("SENSOR_RETRY_DELAY", 0.05)
MAX_SENSOR_ATTEMPTS = _get_int_env("MAX_SENSOR_ATTEMPTS", 3)
LOOP_IDLE_DELAY = _get_float_env("LOOP_IDLE_DELAY", 0.5)
BMS_POLL_INTERVAL = _get_float_env("BMS_POLL_INTERVAL", 1.0)
ENABLED_SENSOR_TYPES = _coerce_str_list(_get_env("ENABLED_SENSOR_TYPES", []))
SENSOR_FAILURE_BACKOFF_SECONDS = _get_float_env("SENSOR_FAILURE_BACKOFF_SECONDS", 0.0)
SENSOR_READ_HARD_TIMEOUT = _get_float_env("SENSOR_READ_HARD_TIMEOUT", 6.0)
BMS_READ_HARD_TIMEOUT = _get_float_env("BMS_READ_HARD_TIMEOUT", 25.0)
OUTPUT_ACTIONS_PER_CYCLE = _get_int_env("OUTPUT_ACTIONS_PER_CYCLE", 1)
OUTPUT_RETRY_DELAY = _get_float_env("OUTPUT_RETRY_DELAY", 1.5)
OUTPUT_RESPONSE_TIMEOUT = _get_float_env("OUTPUT_RESPONSE_TIMEOUT", 0.4)
OUTPUT_SETTLE_DELAY = _get_float_env("OUTPUT_SETTLE_DELAY", 0.05)
OUTPUT_MAX_RETRIES = _get_int_env("OUTPUT_MAX_RETRIES", 3)
PHOTOELECTRIC_POLL_INTERVAL = _get_float_env("PHOTOELECTRIC_POLL_INTERVAL", 1.5)
OBSTACLE_OUTPUT_DELAY = _get_float_env("OBSTACLE_OUTPUT_DELAY", 2.0)
OUTPUT_STARTUP_HOLDOFF_SECONDS = _get_float_env("OUTPUT_STARTUP_HOLDOFF_SECONDS", 12.0)
OUTPUT_IDLE_GAP_SECONDS = _get_float_env("OUTPUT_IDLE_GAP_SECONDS", 0.6)
IO_DEGRADED_HOLDOFF_SECONDS = _get_float_env("IO_DEGRADED_HOLDOFF_SECONDS", 60.0)
PHOTOELECTRIC_AFTER_OUTPUT_GAP_SECONDS = _get_float_env("PHOTOELECTRIC_AFTER_OUTPUT_GAP_SECONDS", 1.0)
STARTUP_IO_OUTPUTS_ENABLED = _get_bool_env("STARTUP_IO_OUTPUTS_ENABLED", False)
CAMERA_LIGHT_OUTPUT_ENABLED = _get_bool_env("CAMERA_LIGHT_OUTPUT_ENABLED", False)
ENV_SENSOR_GROUP_INTERVAL = _get_float_env("ENV_SENSOR_GROUP_INTERVAL", 12.0)
GAS_SENSOR_GROUP_INTERVAL = _get_float_env("GAS_SENSOR_GROUP_INTERVAL", 20.0)
BMS_GROUP_INTERVAL = _get_float_env("BMS_GROUP_INTERVAL", 10.0)
BMS_FAST_GROUP_INTERVAL = _get_float_env("BMS_FAST_GROUP_INTERVAL", 20.0)
BMS_SLOW_GROUP_INTERVAL = _get_float_env("BMS_SLOW_GROUP_INTERVAL", 45.0)
ENV_SENSOR_GROUP_PHASE_OFFSET = _get_float_env("ENV_SENSOR_GROUP_PHASE_OFFSET", 0.0)
GAS_SENSOR_GROUP_PHASE_OFFSET = _get_float_env("GAS_SENSOR_GROUP_PHASE_OFFSET", 2.0)
PHOTOELECTRIC_GROUP_PHASE_OFFSET = _get_float_env("PHOTOELECTRIC_GROUP_PHASE_OFFSET", 4.0)
BMS_FAST_GROUP_PHASE_OFFSET = _get_float_env("BMS_FAST_GROUP_PHASE_OFFSET", 6.0)
BMS_SLOW_GROUP_PHASE_OFFSET = _get_float_env("BMS_SLOW_GROUP_PHASE_OFFSET", 12.0)
ADDRESS_SUSPECT_FAILURES = _get_int_env("ADDRESS_SUSPECT_FAILURES", 4)
ADDRESS_DEGRADED_FAILURES = _get_int_env("ADDRESS_DEGRADED_FAILURES", 8)
ADDRESS_RECOVERY_SUCCESSES = _get_int_env("ADDRESS_RECOVERY_SUCCESSES", 2)
ADDRESS_SUSPECT_BACKOFF_SECONDS = _get_float_env("ADDRESS_SUSPECT_BACKOFF_SECONDS", 8.0)
ADDRESS_DEGRADED_BACKOFF_SECONDS = _get_float_env("ADDRESS_DEGRADED_BACKOFF_SECONDS", 20.0)
SERIAL_STALL_HOLDOFF_SECONDS = _get_float_env("SERIAL_STALL_HOLDOFF_SECONDS", 4.0)
SENSOR_STALE_MAX_AGE_SECONDS = _get_float_env("SENSOR_STALE_MAX_AGE_SECONDS", 12.0)
GATEWAY_HEARTBEAT_SECONDS = _get_float_env("GATEWAY_HEARTBEAT_SECONDS", 30.0)
GATEWAY_WATCHDOG_ENABLED = _get_bool_env("GATEWAY_WATCHDOG_ENABLED", True)
GATEWAY_WATCHDOG_TIMEOUT = _get_float_env("GATEWAY_WATCHDOG_TIMEOUT", 120.0)
GATEWAY_EXIT_ON_BLOCKING_TIMEOUT = _get_bool_env("GATEWAY_EXIT_ON_BLOCKING_TIMEOUT", False)
MQTT_DEGRADE_ENTER_SECONDS = _get_float_env("MQTT_DEGRADE_ENTER_SECONDS", 60.0)
MQTT_DEGRADE_EXIT_SECONDS = _get_float_env("MQTT_DEGRADE_EXIT_SECONDS", 15.0)
MQTT_DEGRADE_FACTOR = _get_float_env("MQTT_DEGRADE_FACTOR", 3.0)

if _RUNTIME_OVERRIDES:
    SENSOR_POLL_DELAY = _apply_override("SENSOR_POLL_DELAY", SENSOR_POLL_DELAY, float)
    SENSOR_RETRY_DELAY = _apply_override("SENSOR_RETRY_DELAY", SENSOR_RETRY_DELAY, float)
    MAX_SENSOR_ATTEMPTS = _apply_override("MAX_SENSOR_ATTEMPTS", MAX_SENSOR_ATTEMPTS, int)
    LOOP_IDLE_DELAY = _apply_override("LOOP_IDLE_DELAY", LOOP_IDLE_DELAY, float)
    BMS_POLL_INTERVAL = _apply_override("BMS_POLL_INTERVAL", BMS_POLL_INTERVAL, float)
    ENABLED_SENSOR_TYPES = _apply_override(
        "ENABLED_SENSOR_TYPES",
        ENABLED_SENSOR_TYPES,
        _coerce_str_list,
    )
    SENSOR_FAILURE_BACKOFF_SECONDS = _apply_override(
        "SENSOR_FAILURE_BACKOFF_SECONDS",
        SENSOR_FAILURE_BACKOFF_SECONDS,
        float,
    )
    SENSOR_READ_HARD_TIMEOUT = _apply_override(
        "SENSOR_READ_HARD_TIMEOUT",
        SENSOR_READ_HARD_TIMEOUT,
        float,
    )
    BMS_READ_HARD_TIMEOUT = _apply_override(
        "BMS_READ_HARD_TIMEOUT",
        BMS_READ_HARD_TIMEOUT,
        float,
    )
    OUTPUT_ACTIONS_PER_CYCLE = _apply_override(
        "OUTPUT_ACTIONS_PER_CYCLE",
        OUTPUT_ACTIONS_PER_CYCLE,
        int,
    )
    OUTPUT_MAX_RETRIES = _apply_override(
        "OUTPUT_MAX_RETRIES",
        OUTPUT_MAX_RETRIES,
        int,
    )
    OUTPUT_RETRY_DELAY = _apply_override(
        "OUTPUT_RETRY_DELAY",
        OUTPUT_RETRY_DELAY,
        float,
    )
    OUTPUT_RESPONSE_TIMEOUT = _apply_override(
        "OUTPUT_RESPONSE_TIMEOUT",
        OUTPUT_RESPONSE_TIMEOUT,
        float,
    )
    OUTPUT_SETTLE_DELAY = _apply_override(
        "OUTPUT_SETTLE_DELAY",
        OUTPUT_SETTLE_DELAY,
        float,
    )
    PHOTOELECTRIC_POLL_INTERVAL = _apply_override(
        "PHOTOELECTRIC_POLL_INTERVAL",
        PHOTOELECTRIC_POLL_INTERVAL,
        float,
    )
    OBSTACLE_OUTPUT_DELAY = _apply_override(
        "OBSTACLE_OUTPUT_DELAY",
        OBSTACLE_OUTPUT_DELAY,
        float,
    )
    OUTPUT_STARTUP_HOLDOFF_SECONDS = _apply_override(
        "OUTPUT_STARTUP_HOLDOFF_SECONDS",
        OUTPUT_STARTUP_HOLDOFF_SECONDS,
        float,
    )
    OUTPUT_IDLE_GAP_SECONDS = _apply_override(
        "OUTPUT_IDLE_GAP_SECONDS",
        OUTPUT_IDLE_GAP_SECONDS,
        float,
    )
    IO_DEGRADED_HOLDOFF_SECONDS = _apply_override(
        "IO_DEGRADED_HOLDOFF_SECONDS",
        IO_DEGRADED_HOLDOFF_SECONDS,
        float,
    )
    PHOTOELECTRIC_AFTER_OUTPUT_GAP_SECONDS = _apply_override(
        "PHOTOELECTRIC_AFTER_OUTPUT_GAP_SECONDS",
        PHOTOELECTRIC_AFTER_OUTPUT_GAP_SECONDS,
        float,
    )
    STARTUP_IO_OUTPUTS_ENABLED = _apply_override(
        "STARTUP_IO_OUTPUTS_ENABLED",
        STARTUP_IO_OUTPUTS_ENABLED,
        _cast_bool,
    )
    CAMERA_LIGHT_OUTPUT_ENABLED = _apply_override(
        "CAMERA_LIGHT_OUTPUT_ENABLED",
        CAMERA_LIGHT_OUTPUT_ENABLED,
        _cast_bool,
    )
    ENV_SENSOR_GROUP_INTERVAL = _apply_override(
        "ENV_SENSOR_GROUP_INTERVAL",
        ENV_SENSOR_GROUP_INTERVAL,
        float,
    )
    GAS_SENSOR_GROUP_INTERVAL = _apply_override(
        "GAS_SENSOR_GROUP_INTERVAL",
        GAS_SENSOR_GROUP_INTERVAL,
        float,
    )
    BMS_GROUP_INTERVAL = _apply_override(
        "BMS_GROUP_INTERVAL",
        BMS_GROUP_INTERVAL,
        float,
    )
    BMS_FAST_GROUP_INTERVAL = _apply_override(
        "BMS_FAST_GROUP_INTERVAL",
        BMS_FAST_GROUP_INTERVAL,
        float,
    )
    BMS_SLOW_GROUP_INTERVAL = _apply_override(
        "BMS_SLOW_GROUP_INTERVAL",
        BMS_SLOW_GROUP_INTERVAL,
        float,
    )
    ENV_SENSOR_GROUP_PHASE_OFFSET = _apply_override(
        "ENV_SENSOR_GROUP_PHASE_OFFSET",
        ENV_SENSOR_GROUP_PHASE_OFFSET,
        float,
    )
    GAS_SENSOR_GROUP_PHASE_OFFSET = _apply_override(
        "GAS_SENSOR_GROUP_PHASE_OFFSET",
        GAS_SENSOR_GROUP_PHASE_OFFSET,
        float,
    )
    PHOTOELECTRIC_GROUP_PHASE_OFFSET = _apply_override(
        "PHOTOELECTRIC_GROUP_PHASE_OFFSET",
        PHOTOELECTRIC_GROUP_PHASE_OFFSET,
        float,
    )
    BMS_FAST_GROUP_PHASE_OFFSET = _apply_override(
        "BMS_FAST_GROUP_PHASE_OFFSET",
        BMS_FAST_GROUP_PHASE_OFFSET,
        float,
    )
    BMS_SLOW_GROUP_PHASE_OFFSET = _apply_override(
        "BMS_SLOW_GROUP_PHASE_OFFSET",
        BMS_SLOW_GROUP_PHASE_OFFSET,
        float,
    )
    ADDRESS_SUSPECT_FAILURES = _apply_override(
        "ADDRESS_SUSPECT_FAILURES",
        ADDRESS_SUSPECT_FAILURES,
        int,
    )
    ADDRESS_DEGRADED_FAILURES = _apply_override(
        "ADDRESS_DEGRADED_FAILURES",
        ADDRESS_DEGRADED_FAILURES,
        int,
    )
    ADDRESS_RECOVERY_SUCCESSES = _apply_override(
        "ADDRESS_RECOVERY_SUCCESSES",
        ADDRESS_RECOVERY_SUCCESSES,
        int,
    )
    ADDRESS_SUSPECT_BACKOFF_SECONDS = _apply_override(
        "ADDRESS_SUSPECT_BACKOFF_SECONDS",
        ADDRESS_SUSPECT_BACKOFF_SECONDS,
        float,
    )
    ADDRESS_DEGRADED_BACKOFF_SECONDS = _apply_override(
        "ADDRESS_DEGRADED_BACKOFF_SECONDS",
        ADDRESS_DEGRADED_BACKOFF_SECONDS,
        float,
    )
    SERIAL_STALL_HOLDOFF_SECONDS = _apply_override(
        "SERIAL_STALL_HOLDOFF_SECONDS",
        SERIAL_STALL_HOLDOFF_SECONDS,
        float,
    )
    SENSOR_STALE_MAX_AGE_SECONDS = _apply_override(
        "SENSOR_STALE_MAX_AGE_SECONDS",
        SENSOR_STALE_MAX_AGE_SECONDS,
        float,
    )
    GATEWAY_HEARTBEAT_SECONDS = _apply_override(
        "GATEWAY_HEARTBEAT_SECONDS",
        GATEWAY_HEARTBEAT_SECONDS,
        float,
    )
    GATEWAY_WATCHDOG_ENABLED = _apply_override(
        "GATEWAY_WATCHDOG_ENABLED",
        GATEWAY_WATCHDOG_ENABLED,
        _cast_bool,
    )
    GATEWAY_WATCHDOG_TIMEOUT = _apply_override(
        "GATEWAY_WATCHDOG_TIMEOUT",
        GATEWAY_WATCHDOG_TIMEOUT,
        float,
    )
    GATEWAY_EXIT_ON_BLOCKING_TIMEOUT = _apply_override(
        "GATEWAY_EXIT_ON_BLOCKING_TIMEOUT",
        GATEWAY_EXIT_ON_BLOCKING_TIMEOUT,
        _cast_bool,
    )
    MQTT_DEGRADE_ENTER_SECONDS = _apply_override(
        "MQTT_DEGRADE_ENTER_SECONDS",
        MQTT_DEGRADE_ENTER_SECONDS,
        float,
    )
    MQTT_DEGRADE_EXIT_SECONDS = _apply_override(
        "MQTT_DEGRADE_EXIT_SECONDS",
        MQTT_DEGRADE_EXIT_SECONDS,
        float,
    )
    MQTT_DEGRADE_FACTOR = _apply_override(
        "MQTT_DEGRADE_FACTOR",
        MQTT_DEGRADE_FACTOR,
        float,
    )
