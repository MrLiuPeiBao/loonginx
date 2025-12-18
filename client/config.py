"""Configuration for the sensor gateway."""

MQTT_CONFIG = {
    'broker': '192.168.12.21',
    'port': 1883,
    'client_id': 'loongson_sensor_gateway',
    'username': None,
    'password': None,
    'keepalive': 60,
}

MQTT_TOPICS = {
    'sensor_data': 'sensors/data',
    'bms_data': 'sensors/bms',
    'rfid_data': 'sensors/rfid',
    'command_request': 'sensors/command/request',
    'command_response': 'sensors/command/response',
}

SERIAL_CONFIG = {
    'port': '/dev/ttyS4',
    'baudrate': 9600,
    'timeout': 0.5,
}

RFID_SERIAL_CONFIG = {
    'port': '/dev/ttyS0',
    'baudrate': 9600,
}

SENSOR_CONFIGS = {
    'temperature': {
        'address': 30,
        'register_addr': 16,
        'registers': 2,
        'parse_type': 'dcba',
        'function_code': 3,
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
    'address': 210,
    'function_code': 3,
    'max_retries': 3,
    'response_delay': 0.03,
    'response_timeout': 1.0,
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
