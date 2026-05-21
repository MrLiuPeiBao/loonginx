from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .base_sensor import BaseSensor


class PhotoelectricSensor(BaseSensor):
    """Photoelectric obstacle sensor connected through the IO board."""

    def __init__(self, serial_manager, config: dict):
        super().__init__("photoelectric", config["address"], serial_manager)
        command_hex = str(config.get("command_hex") or "19 02 00 00 00 02 FA 13")
        self.command = bytes.fromhex(command_hex)
        self.response_timeout = config.get("response_timeout")
        self.max_attempts = config.get("max_attempts")

    def read_data(self) -> Optional[bytes]:
        return self.serial_manager.send_raw_command_with_options(
            self.command,
            response_timeout=self.response_timeout,
        )

    def parse_data(self, raw_data) -> Optional[dict]:
        if not raw_data or len(raw_data) < 6:
            return None

        frame = bytes(raw_data)
        if frame[0] != self.address:
            logging.error(
                "Photoelectric response address mismatch response=%s expected=%s",
                frame[0],
                self.address,
            )
            return None
        if frame[1] != 0x02:
            logging.error("Photoelectric response function mismatch function=%s", frame[1])
            return None
        if frame[2] < 1:
            logging.error("Photoelectric response missing status byte frame=%s", frame.hex(" "))
            return None

        raw_status = frame[3]
        if raw_status == 0:
            front_obstacle = False
            rear_obstacle = False
        elif raw_status == 1:
            front_obstacle = True
            rear_obstacle = False
        elif raw_status == 2:
            front_obstacle = False
            rear_obstacle = True
        elif raw_status == 3:
            front_obstacle = True
            rear_obstacle = True
        else:
            logging.error("Photoelectric response unknown status=%s frame=%s", raw_status, frame.hex(" "))
            return None

        obstacle_count = int(front_obstacle) + int(rear_obstacle)
        return {
            "value": obstacle_count,
            "obstacle_detected": obstacle_count > 0,
            "left_obstacle": front_obstacle,
            "right_obstacle": rear_obstacle,
            "front_obstacle": front_obstacle,
            "rear_obstacle": rear_obstacle,
            "raw_mask": raw_status,
            "raw_hex": frame.hex(" "),
        }

    def get_formatted_data(self) -> Optional[dict]:
        raw_data = self.read_data()
        if raw_data is None:
            return None

        parsed_data = self.parse_data(raw_data)
        if parsed_data is None:
            return None

        self.last_value = parsed_data
        now = datetime.now().astimezone()
        self._last_success_ts = now.timestamp()
        payload = {
            "sensor_type": self.name,
            "address": self.address,
            "value": parsed_data["value"],
            "obstacle_detected": parsed_data["obstacle_detected"],
            "left_obstacle": parsed_data["left_obstacle"],
            "right_obstacle": parsed_data["right_obstacle"],
            "front_obstacle": parsed_data["front_obstacle"],
            "rear_obstacle": parsed_data["rear_obstacle"],
            "raw_mask": parsed_data["raw_mask"],
            "raw_hex": parsed_data["raw_hex"],
            "timestamp": now.isoformat(),
            "ts": now.timestamp(),
        }
        self._last_payload = dict(payload)
        return payload
