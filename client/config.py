"""Configuration for the sensor gateway."""

import json
import os

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv:
    load_dotenv()

from utils.config_override import load_overrides


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
    for item in raw.split(','):
        if '=' not in item:
            continue
        key, val = item.split('=', 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        try:
            result[key] = int(val)
        except Exception:
            continue
    return result if result else default


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
    'broker': _get_env('MQTT_BROKER', '192.168.12.21'),  # MQTT Broker 地址
    'port': _get_int_env('MQTT_PORT', 1883),  # MQTT 端口
    'client_id': _get_env('MQTT_CLIENT_ID', 'loongson_sensor_gateway'),  # 下位机标识
    'username': _get_env('MQTT_USERNAME', None),  # 用户名（可空）
    'password': _get_env('MQTT_PASSWORD', None),  # 密码（可空）
    'keepalive': _get_int_env('MQTT_KEEPALIVE', 60),  # 心跳间隔（秒）
    'publish_qos': _get_int_env('MQTT_PUBLISH_QOS', 0),  # 发布 QoS（0/1）
    'offline_queue_enabled': _get_bool_env('MQTT_OFFLINE_QUEUE_ENABLED', False),  # 离线缓存开关
    'offline_queue_max_items': _get_int_env('MQTT_OFFLINE_QUEUE_MAX_ITEMS', 500),  # 缓存最大条数
    'offline_queue_max_bytes': _get_int_env('MQTT_OFFLINE_QUEUE_MAX_BYTES', 2_000_000),  # 缓存最大字节数
    'topic_qos_map': _get_json_or_kv_env('MQTT_TOPIC_QOS_MAP', {}),  # 按 topic 配置 QoS
    'offline_policy': _get_str_env('MQTT_OFFLINE_POLICY', 'queue'),  # queue | latest
}

_RUNTIME_OVERRIDES = load_overrides()
if _RUNTIME_OVERRIDES:
    mqtt_override_map = {
        'MQTT_BROKER': ('broker', str),
        'MQTT_PORT': ('port', int),
        'MQTT_CLIENT_ID': ('client_id', str),
        'MQTT_USERNAME': ('username', str),
        'MQTT_PASSWORD': ('password', str),
        'MQTT_KEEPALIVE': ('keepalive', int),
        'MQTT_PUBLISH_QOS': ('publish_qos', int),
        'MQTT_OFFLINE_QUEUE_ENABLED': ('offline_queue_enabled', _cast_bool),
        'MQTT_OFFLINE_QUEUE_MAX_ITEMS': ('offline_queue_max_items', int),
        'MQTT_OFFLINE_QUEUE_MAX_BYTES': ('offline_queue_max_bytes', int),
        'MQTT_OFFLINE_POLICY': ('offline_policy', str),
    }
    for key, (field, caster) in mqtt_override_map.items():
        MQTT_CONFIG[field] = _apply_override(key, MQTT_CONFIG[field], caster)

MQTT_TOPIC_QOS_MAP = MQTT_CONFIG['topic_qos_map']
MQTT_OFFLINE_POLICY = MQTT_CONFIG['offline_policy']

MQTT_TOPICS = {
    'sensor_data': 'sensors/data',  # 传感器数据
    'bms_data': 'sensors/bms',  # BMS 数据
    'rfid_data': 'sensors/rfid',  # RFID 数据
    'command_request': 'sensors/command/request',  # 串口命令下发
    'command_response': 'sensors/command/response',  # 串口命令回执
    'cableway_status': 'cableway/status',  # 索道 PLC 状态
    'cableway_command_request': 'cableway/command/request',  # 索道 PLC 命令下发
    'cableway_command_response': 'cableway/command/response',  # 索道 PLC 命令回执
    'config_update': 'config/update',  # 配置下发
    'config_ack': 'config/ack',  # 配置回执
    'device_hello': 'device/hello',  # 设备启动握手
}

PLC_CONFIG = {
    # Siemens S7-200 SMART PLC (cableway motion control), Modbus TCP server.
    'enabled': _get_bool_env('PLC_ENABLED', True),  # 是否启用 PLC
    'host': _get_env('PLC_HOST', '192.168.2.1'),  # PLC IP
    'port': _get_int_env('PLC_PORT', 502),  # PLC 端口
    'unit_id': _get_int_env('PLC_UNIT_ID', 1),  # Modbus 站号
    'timeout': _get_float_env('PLC_TIMEOUT', 2.0),  # 通信超时（秒）
    'connect_timeout': _get_float_env('PLC_CONNECT_TIMEOUT', 2.0),  # 连接超时（秒）
    # Data layout tuning knobs (keep defaults unless现场验证需要调整):
    # - even_byte_is_high: VB 偶地址字节是否对应寄存器高字节（常见为 True）。
    # - float_word_order/float_byte_order: REAL(32-bit float) 的字/字节序。
    'even_byte_is_high': _get_bool_env('PLC_EVEN_BYTE_IS_HIGH', True),  # 偶地址字节是否为高字节
    'float_word_order': str(_get_env('PLC_FLOAT_WORD_ORDER', 'big')).lower(),  # REAL 字序
    'float_byte_order': str(_get_env('PLC_FLOAT_BYTE_ORDER', 'big')).lower(),  # REAL 字节序
    # Poll status bits and publish to MQTT at this interval (seconds).
    'status_poll_interval': _get_float_env('PLC_STATUS_POLL_INTERVAL', 0.5),  # 状态轮询间隔（秒）
    # Write heartbeat (VW2414) at this interval (seconds), alternating 0/5.
    'heartbeat_interval': _get_float_env('PLC_HEARTBEAT_INTERVAL', 1.0),  # 心跳写入间隔（秒）
    # Pulse commands: write value then reset to 0 after this delay.
    'command_pulse_seconds': _get_float_env('PLC_COMMAND_PULSE_SECONDS', 0.1),  # 脉冲写入时长（秒）
}

SERIAL_DIRECT_RETRIES = _get_int_env('SERIAL_DIRECT_RETRIES', 3)
SERIAL_DIRECT_RESPONSE_DELAY = _get_float_env('SERIAL_DIRECT_RESPONSE_DELAY', 0.02)
SERIAL_DIRECT_TIMEOUT = _get_float_env('SERIAL_DIRECT_TIMEOUT', 1.0)

SERIAL_CONFIG = {
    'port': _get_env('SERIAL_PORT', '/dev/ttyS4'),  # 传感器串口
    'baudrate': _get_int_env('SERIAL_BAUDRATE', 9600),  # 波特率
    'timeout': _get_float_env('SERIAL_TIMEOUT', 0.5),  # 读超时（秒）
    'direct_retries': SERIAL_DIRECT_RETRIES,
    'direct_response_delay': SERIAL_DIRECT_RESPONSE_DELAY,
    'direct_timeout': SERIAL_DIRECT_TIMEOUT,
}

RFID_SERIAL_CONFIG = {
    'port': _get_env('RFID_SERIAL_PORT', '/dev/ttyS0'),  # RFID 串口
    'baudrate': _get_int_env('RFID_SERIAL_BAUDRATE', 9600),  # 波特率
}

SENSOR_CONFIGS = {
    'temperature': {
        'address': 30,  # 设备地址
        'register_addr': 16,  # 寄存器起始地址
        'registers': 2,  # 寄存器数量
        'parse_type': 'dcba',  # 解析方式
        'function_code': 3,  # 功能码
    },
    'humidity': {
        'address': 31,
        'register_addr': 16,
        'registers': 2,
        'parse_type': 'dcba',
        'function_code': 3,
    },
    'pressure': {
        'address': 32,
        'register_addr': 16,
        'registers': 2,
        'parse_type': 'dcba',
        'function_code': 3,
    },
    'co': {
        'address': 1,
        'register_addr': 0x65,
        'registers': 1,
        'parse_type': 'raw',
        'function_code': 3,
    },
    'h2s': {
        'address': 2,
        'register_addr': 0x65,
        'registers': 1,
        'parse_type': 'raw',
        'function_code': 3,
    },
    'o2': {
        'address': 3,
        'register_addr': 0x65,
        'registers': 1,
        'parse_type': 'o2',
        'function_code': 3,
    },
    'ch4': {
        'address': 4,
        'register_addr': 0x65,
        'registers': 1,
        'parse_type': 'raw',
        'function_code': 3,
    },
    'smoke': {
        'address': 5,
        'register_addr': 0x00,
        'registers': 1,
        'parse_type': 'smoke',
        'function_code': 3,
    },
}

BMS_CONFIG = {
    'address': 210,  # BMS 设备地址
    'function_code': 3,  # 功能码
    'max_retries': 3,  # 失败重试次数
    'response_delay': 0.03,  # 写请求后等待时间（秒）
    'response_timeout': 1.0,  # 单帧响应超时（秒）
    'registers': {
        'voltage': {'addr': 0x28, 'multiplier': 0.1},
        'soc': {'addr': 0x2A, 'multiplier': 0.001},
        'status': {'addr': 0x2F, 'multiplier': 1},
        'capacity': {'addr': 0x30, 'multiplier': 0.1},
        'power': {'addr': 0x39, 'multiplier': 1},
        'cell_voltages': {
            'addr': 0x00,
            'registers': 8,
            'multiplier': 0.001,
            'precision': 3,
        },
        'current': {
            'addr': 0x29,
            'multiplier': 0.1,
            'offset': -30000,
            'precision': 1,
        },
    },
}

MESSAGE_SCHEMA_VERSION = _get_int_env('MESSAGE_SCHEMA_VERSION', 1)  # 消息结构版本号

SENSOR_POLL_DELAY = _get_float_env('SENSOR_POLL_DELAY', 0.08)
SENSOR_RETRY_DELAY = _get_float_env('SENSOR_RETRY_DELAY', 0.08)
MAX_SENSOR_ATTEMPTS = _get_int_env('MAX_SENSOR_ATTEMPTS', 3)
LOOP_IDLE_DELAY = _get_float_env('LOOP_IDLE_DELAY', 0.5)
BMS_POLL_INTERVAL = _get_float_env('BMS_POLL_INTERVAL', 1.0)
MQTT_DEGRADE_ENTER_SECONDS = _get_float_env('MQTT_DEGRADE_ENTER_SECONDS', 60.0)
MQTT_DEGRADE_EXIT_SECONDS = _get_float_env('MQTT_DEGRADE_EXIT_SECONDS', 15.0)
MQTT_DEGRADE_FACTOR = _get_float_env('MQTT_DEGRADE_FACTOR', 3.0)

if _RUNTIME_OVERRIDES:
    SENSOR_POLL_DELAY = _apply_override('SENSOR_POLL_DELAY', SENSOR_POLL_DELAY, float)
    SENSOR_RETRY_DELAY = _apply_override('SENSOR_RETRY_DELAY', SENSOR_RETRY_DELAY, float)
    MAX_SENSOR_ATTEMPTS = _apply_override('MAX_SENSOR_ATTEMPTS', MAX_SENSOR_ATTEMPTS, int)
    LOOP_IDLE_DELAY = _apply_override('LOOP_IDLE_DELAY', LOOP_IDLE_DELAY, float)
    BMS_POLL_INTERVAL = _apply_override('BMS_POLL_INTERVAL', BMS_POLL_INTERVAL, float)
    MQTT_DEGRADE_ENTER_SECONDS = _apply_override(
        'MQTT_DEGRADE_ENTER_SECONDS',
        MQTT_DEGRADE_ENTER_SECONDS,
        float,
    )
    MQTT_DEGRADE_EXIT_SECONDS = _apply_override(
        'MQTT_DEGRADE_EXIT_SECONDS',
        MQTT_DEGRADE_EXIT_SECONDS,
        float,
    )
    MQTT_DEGRADE_FACTOR = _apply_override(
        'MQTT_DEGRADE_FACTOR',
        MQTT_DEGRADE_FACTOR,
        float,
    )
