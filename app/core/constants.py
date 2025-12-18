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
    'person_image': 'yolo/person_img',
    'annotated_person_image': 'yolo/annotated_img',
}
