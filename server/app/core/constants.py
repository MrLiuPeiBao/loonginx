"""Project-wide constants."""

from __future__ import annotations

from typing import Dict

MQTT_TOPICS: Dict[str, str] = {
    'sensor_data': 'sensors/data',
    'bms_data': 'sensors/bms',
    'rfid_data': 'sensors/rfid',
    'alarm_broadcast': 'sensors/alarms',
    'command_request': 'sensors/command/request',
    'command_response': 'sensors/command/response',
    'cableway_status': 'cableway/status',
    'person_image': 'yolo/person_img',
    'annotated_person_image': 'yolo/annotated_img',
    'config_update': 'config/update',
    'config_ack': 'config/ack',
    'device_hello': 'device/hello',
}
