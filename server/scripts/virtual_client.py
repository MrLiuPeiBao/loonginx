#!/usr/bin/env python3
"""Virtual MQTT client that simulates lower-level gateway telemetry."""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import math
import os
import struct
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

import paho.mqtt.client as mqtt
import requests
from PIL import Image, ImageDraw


MQTT_TOPICS: Dict[str, str] = {
    "sensor_data": "sensors/data",
    "bms_data": "sensors/bms",
    "rfid_data": "sensors/rfid",
    "command_request": "sensors/command/request",
    "command_response": "sensors/command/response",
    "cableway_status": "cableway/status",
    "cableway_command_request": "cableway/command/request",
    "cableway_command_response": "cableway/command/response",
    "config_update": "config/update",
    "config_ack": "config/ack",
    "device_hello": "device/hello",
}

HOT_UPDATE_KEYS = {
    "SENSOR_POLL_DELAY",
    "SENSOR_RETRY_DELAY",
    "MAX_SENSOR_ATTEMPTS",
    "LOOP_IDLE_DELAY",
    "BMS_POLL_INTERVAL",
    "MQTT_DEGRADE_ENTER_SECONDS",
    "MQTT_DEGRADE_EXIT_SECONDS",
    "MQTT_DEGRADE_FACTOR",
    "MQTT_PUBLISH_QOS",
    "MQTT_OFFLINE_QUEUE_ENABLED",
    "MQTT_OFFLINE_QUEUE_MAX_ITEMS",
    "MQTT_OFFLINE_QUEUE_MAX_BYTES",
    "MQTT_OFFLINE_POLICY",
    "MQTT_TOPIC_QOS_MAP",
}

DEFAULT_SENSOR_CONFIGS = (
    {
        "type": "temperature",
        "version": 1,
        "description": "Temperature",
        "unit": "C",
        "min_threshold": 10.0,
        "max_threshold": 35.0,
    },
    {
        "type": "humidity",
        "version": 1,
        "description": "Humidity",
        "unit": "%",
        "min_threshold": 20.0,
        "max_threshold": 80.0,
    },
    {
        "type": "pressure",
        "version": 1,
        "description": "Pressure",
        "unit": "kPa",
        "min_threshold": 90.0,
        "max_threshold": 110.0,
    },
    {
        "type": "smoke",
        "version": 1,
        "description": "Smoke",
        "unit": "ppm",
        "min_threshold": 0.0,
        "max_threshold": 5.0,
    },
    {
        "type": "co",
        "version": 1,
        "description": "CO",
        "unit": "ppm",
        "min_threshold": 0.0,
        "max_threshold": 35.0,
    },
    {
        "type": "o2",
        "version": 1,
        "description": "O2",
        "unit": "%",
        "min_threshold": 19.5,
        "max_threshold": 23.5,
    },
    {
        "type": "h2s",
        "version": 1,
        "description": "H2S",
        "unit": "ppm",
        "min_threshold": 0.0,
        "max_threshold": 10.0,
    },
    {
        "type": "ch4",
        "version": 1,
        "description": "CH4",
        "unit": "%",
        "min_threshold": 0.0,
        "max_threshold": 1.0,
    },
)

@dataclass
class SimulatorConfig:
    mqtt_broker: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_client_id: str
    api_base_url: str
    location: str
    schema_version: int
    sensor_interval: float
    bms_interval: float
    rfid_interval: float
    cableway_interval: float
    hello_interval: float
    media_interval: float
    metal_interval: float
    command_lookup_window: int
    duration_seconds: float
    seed_sensor_configs: bool
    enable_http_seed: bool


class VirtualClientSimulator:
    def __init__(self, config: SimulatorConfig):
        self.config = config
        self.logger = logging.getLogger("virtual_client")
        self.start_monotonic = time.monotonic()
        self.stop_event = threading.Event()
        self.connected_event = threading.Event()
        self.client = mqtt.Client(client_id=config.mqtt_client_id)
        self.client.enable_logger(self.logger)
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        if config.mqtt_username:
            self.client.username_pw_set(config.mqtt_username, config.mqtt_password or None)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        self.http = requests.Session()
        self.current_location = config.location
        self.last_rfid_card = config.location
        self.config_version = 0
        self.handled_generic_request_ids: set[str] = set()
        self.last_control_code = 88
        self.last_param_updates: Dict[str, float] = {}
        self.motion_direction = "idle"

        now = time.monotonic()
        self.next_sensor_at = now
        self.next_bms_at = now
        self.next_rfid_at = now
        self.next_cableway_at = now
        self.next_hello_at = now
        self.next_media_at = now + 2.0
        self.next_metal_at = now + 4.0

    def run(self) -> None:
        self.logger.info(
            "Starting virtual client mqtt=%s:%s api=%s device_id=%s",
            self.config.mqtt_broker,
            self.config.mqtt_port,
            self.config.api_base_url,
            self.config.mqtt_client_id,
        )
        if self.config.seed_sensor_configs and self.config.enable_http_seed:
            self.seed_sensor_configs()

        self.client.connect_async(self.config.mqtt_broker, self.config.mqtt_port, keepalive=60)
        self.client.loop_start()
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                elapsed = now - self.start_monotonic
                if self.config.duration_seconds > 0 and elapsed >= self.config.duration_seconds:
                    self.logger.info("Reached duration limit %.1fs, stopping", self.config.duration_seconds)
                    break
                self._tick(now)
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")
        finally:
            self.stop()

    def stop(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        try:
            self.client.loop_stop()
        finally:
            try:
                self.client.disconnect()
            except Exception:
                self.logger.debug("MQTT disconnect failed", exc_info=True)
        self.http.close()

    def _tick(self, now: float) -> None:
        if now >= self.next_hello_at:
            self.publish_device_hello()
            self.next_hello_at = now + self.config.hello_interval

        if not self.connected_event.is_set():
            return

        if now >= self.next_sensor_at:
            self.publish_sensor_data()
            self.next_sensor_at = now + self.config.sensor_interval

        if now >= self.next_bms_at:
            self.publish_bms_data()
            self.next_bms_at = now + self.config.bms_interval

        if now >= self.next_rfid_at:
            self.publish_rfid_data()
            self.next_rfid_at = now + self.config.rfid_interval

        if now >= self.next_cableway_at:
            self.publish_cableway_status()
            self.next_cableway_at = now + self.config.cableway_interval

        if self.config.enable_http_seed and now >= self.next_media_at:
            self.seed_media_payloads()
            self.next_media_at = now + self.config.media_interval

        if self.config.enable_http_seed and now >= self.next_metal_at:
            self.seed_metal_anomaly()
            self.next_metal_at = now + self.config.metal_interval

    def _on_connect(self, client: mqtt.Client, userdata: object, flags: Dict[str, int], rc: int) -> None:
        if rc != 0:
            self.logger.error("MQTT connect failed rc=%s", rc)
            self.connected_event.clear()
            return
        self.connected_event.set()
        self.logger.info("MQTT connected")
        for topic in (
            MQTT_TOPICS["command_request"],
            MQTT_TOPICS["cableway_command_request"],
            MQTT_TOPICS["config_update"],
        ):
            client.subscribe(topic)
        self.publish_device_hello()

    def _on_disconnect(self, client: mqtt.Client, userdata: object, rc: int) -> None:
        self.connected_event.clear()
        if rc == 0:
            self.logger.info("MQTT disconnected gracefully")
        else:
            self.logger.warning("MQTT disconnected rc=%s", rc)

    def _on_message(self, client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage) -> None:
        try:
            if msg.topic == MQTT_TOPICS["command_request"]:
                self.handle_command_request(bytes(msg.payload or b""))
            elif msg.topic == MQTT_TOPICS["cableway_command_request"]:
                self.handle_cableway_command(bytes(msg.payload or b""))
            elif msg.topic == MQTT_TOPICS["config_update"]:
                self.handle_config_update(bytes(msg.payload or b""))
        except Exception:
            self.logger.exception("Failed to process MQTT message topic=%s", msg.topic)

    def publish_device_hello(self) -> None:
        payload = {
            "payload_type": "device_hello",
            "device_id": self.config.mqtt_client_id,
            "config_version": self.config_version,
            "last_ts": self._now_ts(),
            "timestamp": self._now_iso(),
            "ts": self._now_ts(),
            "schema": self.config.schema_version,
        }
        self._publish_json(MQTT_TOPICS["device_hello"], payload)

    def publish_sensor_data(self) -> None:
        elapsed = time.monotonic() - self.start_monotonic
        alarm_window = 8.0 <= (elapsed % 90.0) <= 18.0
        severe_window = 35.0 <= (elapsed % 120.0) <= 42.0
        timestamp = self._now_iso()
        base = {
            "device_id": self.config.mqtt_client_id,
            "location": self.current_location,
            "timestamp": timestamp,
            "schema": self.config.schema_version,
            "payload_type": "sensor_data",
        }
        values = {
            "temperature": 24.0 + 6.0 * math.sin(elapsed / 18.0) + (14.0 if alarm_window else 0.0),
            "humidity": 55.0 + 18.0 * math.sin(elapsed / 25.0 + 1.5),
            "pressure": 101.2 + 3.8 * math.sin(elapsed / 30.0 + 0.4),
            "smoke": 0.3 + (8.0 if severe_window else 0.0),
            "co": 6.0 + 4.0 * math.sin(elapsed / 15.0) + (42.0 if severe_window else 0.0),
            "o2": 20.7 + 0.2 * math.sin(elapsed / 20.0) - (2.4 if alarm_window else 0.0),
            "h2s": 0.8 + 0.4 * math.sin(elapsed / 17.0) + (12.0 if severe_window else 0.0),
            "ch4": 0.1 + 0.15 * math.sin(elapsed / 22.0) + (1.2 if severe_window else 0.0),
        }
        payload = []
        for sensor_type, value in values.items():
            item = dict(base)
            item["sensor_type"] = sensor_type
            item["value"] = round(float(value), 3)
            payload.append(item)
        self._publish_json(MQTT_TOPICS["sensor_data"], payload)

    def publish_bms_data(self) -> None:
        elapsed = time.monotonic() - self.start_monotonic
        low_voltage_window = 20.0 <= (elapsed % 75.0) <= 28.0
        cell_base = 3.23 if low_voltage_window else 3.34
        payload = {
            "payload_type": "bms_data",
            "device_id": self.config.mqtt_client_id,
            "location": self.current_location,
            "timestamp": self._now_iso(),
            "ts": self._now_ts(),
            "schema": self.config.schema_version,
            "voltage": round(43.2 if low_voltage_window else 51.6, 2),
            "soc": round(32.0 + 18.0 * math.sin(elapsed / 60.0), 2),
            "status": 1,
            "capacity": 120.0,
            "power": round(540.0 + 120.0 * math.sin(elapsed / 12.0), 2),
            "current": round(8.5 + 2.4 * math.sin(elapsed / 10.0), 2),
            "cell_voltages": [round(cell_base + (idx * 0.004), 3) for idx in range(8)],
        }
        self._publish_json(MQTT_TOPICS["bms_data"], payload)

    def publish_rfid_data(self) -> None:
        stations = (
            "station-entry-a",
            "station-mid-b",
            "station-exit-c",
            "station-maint-d",
        )
        index = int((time.monotonic() - self.start_monotonic) // max(self.config.rfid_interval, 1.0))
        self.last_rfid_card = stations[index % len(stations)]
        self.current_location = self.last_rfid_card
        payload = {
            "payload_type": "rfid_data",
            "device_id": self.config.mqtt_client_id,
            "location": self.current_location,
            "timestamp": self._now_iso(),
            "ts": self._now_ts(),
            "schema": self.config.schema_version,
            "card_id": self.last_rfid_card,
            "raw_data": self.last_rfid_card,
        }
        self._publish_json(MQTT_TOPICS["rfid_data"], payload)

    def publish_cableway_status(self) -> None:
        elapsed = time.monotonic() - self.start_monotonic
        fault_window = 10.0 <= (elapsed % 70.0) <= 16.0
        heartbeat_fault = 36.0 <= (elapsed % 110.0) <= 40.0
        q_forward = self.motion_direction == "forward"
        q_reverse = self.motion_direction == "reverse"
        faults = {
            "gz_total_fault": fault_window,
            "gz_position_fault": fault_window,
            "gz_home_fault": False,
            "gz_over_positive_limit": False,
            "gz_over_negative_limit": False,
            "gz_deviation_too_large": fault_window,
            "gz_setpoint_overrun": False,
            "gz_hard_limit": False,
            "gz_positive_limit_estop": False,
            "gz_negative_limit_estop": False,
            "gz_estop_fault": False,
            "gz_estop_inhibit_start": False,
            "gz_robot_estop": False,
            "gz_general_fault": fault_window,
            "gz_counterweight_low": False,
            "gz_counterweight_high": False,
            "gz_overspeed": False,
            "gz_vfd_fault": False,
            "gz_brake_fault": False,
        }
        outputs = {
            "q_fault": fault_window,
            "q_reverse": q_reverse,
            "q_forward": q_forward,
            "q_work_brake": not (q_forward or q_reverse),
            "q_warning": fault_window,
            "heartbeat_timeout": heartbeat_fault,
            "command_in_progress": False,
        }
        active_faults = [key for key, enabled in faults.items() if enabled]
        payload = {
            "payload_type": "cableway_status",
            "device_id": self.config.mqtt_client_id,
            "location": self.current_location,
            "timestamp": self._now_iso(),
            "ts": self._now_ts(),
            "schema": self.config.schema_version,
            "plc_host": "virtual-plc.local",
            "status": {
                "faults": faults,
                "outputs": outputs,
                "active_faults": active_faults,
                "last_control_code": self.last_control_code,
                "last_param_updates": self.last_param_updates,
            },
        }
        self._publish_json(MQTT_TOPICS["cableway_status"], payload)

    def handle_command_request(self, payload: bytes) -> None:
        request_hex = self._bytes_to_hex(payload)
        request_id = self.lookup_generic_request_id(request_hex)
        response_bytes = self._build_command_response(payload)
        response = {
            "payload_type": "command_response",
            "device_id": self.config.mqtt_client_id,
            "timestamp": self._now_iso(),
            "ts": self._now_ts(),
            "schema": self.config.schema_version,
            "request_hex": request_hex,
            "response_hex": self._bytes_to_hex(response_bytes),
            "success": True,
        }
        if request_id:
            response["request_id"] = request_id
        self._publish_json(MQTT_TOPICS["command_response"], response)

    def handle_cableway_command(self, payload: bytes) -> None:
        command_type = "unknown"
        request_id = None
        response: Dict[str, Any] = {
            "payload_type": "cableway_command_response",
            "device_id": self.config.mqtt_client_id,
            "timestamp": self._now_iso(),
            "ts": self._now_ts(),
            "schema": self.config.schema_version,
            "success": False,
        }
        try:
            message = json.loads(payload.decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("payload must be an object")
            request_id = message.get("request_id")
            command_type = str(message.get("type") or "").strip() or "unknown"
            response["type"] = command_type
            if command_type == "control":
                self.last_control_code = int(message["command_code"])
                self.motion_direction = self._direction_from_control_code(self.last_control_code)
                response["result"] = {"command_code": self.last_control_code}
            elif command_type == "estop":
                self.last_control_code = 0
                self.motion_direction = "idle"
                response["result"] = {"estop": True}
            elif command_type == "set_params":
                params = message.get("params") or {}
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                self.last_param_updates = {str(key): float(value) for key, value in params.items()}
                response["result"] = {"params": self.last_param_updates}
            elif command_type == "read_status":
                response["result"] = self._current_cableway_status_snapshot()
            else:
                raise ValueError(f"unsupported command type={command_type}")
            response["success"] = True
        except Exception as exc:
            response["error"] = str(exc)
            response["type"] = command_type
            self.logger.warning("Cableway command failed type=%s error=%s", command_type, exc)

        if request_id:
            response["request_id"] = request_id
        self._publish_json(MQTT_TOPICS["cableway_command_response"], response)

    def handle_config_update(self, payload: bytes) -> None:
        response: Dict[str, Any] = {
            "payload_type": "config_ack",
            "device_id": self.config.mqtt_client_id,
            "timestamp": self._now_iso(),
            "ts": self._now_ts(),
            "schema": self.config.schema_version,
            "success": False,
            "status": "failed",
        }
        try:
            message = json.loads(payload.decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("payload must be an object")
            overrides = message.get("payload") or {}
            if not isinstance(overrides, dict):
                raise ValueError("payload field must be an object")
            version = int(message.get("version") or self.config_version)
            applied_keys = sorted(key for key in overrides if key in HOT_UPDATE_KEYS)
            pending_restart_keys = sorted(key for key in overrides if key not in HOT_UPDATE_KEYS)
            self.config_version = version
            self._apply_hot_overrides(overrides)
            response.update(
                {
                    "success": True,
                    "status": "success",
                    "version": version,
                    "applied_keys": applied_keys,
                    "pending_restart_keys": pending_restart_keys,
                }
            )
        except Exception as exc:
            response["error"] = str(exc)
            self.logger.warning("Config update failed error=%s", exc)
        self._publish_json(MQTT_TOPICS["config_ack"], response)

    def seed_sensor_configs(self) -> None:
        for item in DEFAULT_SENSOR_CONFIGS:
            payload = dict(item)
            payload["update_time"] = self._now_iso()
            self._api_request(
                "put",
                f"/config/sensors/{payload['type']}",
                json_payload=payload,
            )

    def seed_media_payloads(self) -> None:
        timestamp = self._now_iso()
        image_payload = [
            {
                "timestamp": timestamp,
                "device_id": f"{self.config.mqtt_client_id}-camera",
                "image_name": f"virtual-person-{int(time.time())}.png",
                "image_data": self._build_png_base64(self.current_location),
                "location": self.current_location,
            }
        ]
        audio_payload = [
            {
                "timestamp": timestamp,
                "device_id": f"{self.config.mqtt_client_id}-mic",
                "audio_name": f"virtual-audio-{int(time.time())}.wav",
                "audio_data": self._build_wav_base64(duration_seconds=1.0),
                "location": self.current_location,
            }
        ]
        self._api_request("post", "/images", json_payload=image_payload)
        self._api_request("post", "/audio", json_payload=audio_payload)

    def seed_metal_anomaly(self) -> None:
        payload = [
            {
                "timestamp": self._now_iso(),
                "device_id": f"{self.config.mqtt_client_id}-metal",
                "location": self.current_location,
            }
        ]
        self._api_request("post", "/metal-anomaly", json_payload=payload)

    def lookup_generic_request_id(self, request_hex: str) -> Optional[str]:
        if not self.config.enable_http_seed:
            return None
        response = self._api_request(
            "get",
            "/command-requests",
            params={"status": "sent", "limit": self.config.command_lookup_window},
        )
        if response is None:
            return None
        try:
            rows = response.json()
        except Exception:
            return None
        if not isinstance(rows, list):
            return None

        normalized_request = self._normalize_hex_string(request_hex)
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("command_type") or "") != "generic":
                continue
            request_id = row.get("request_id")
            if not request_id or request_id in self.handled_generic_request_ids:
                continue
            payload_text = self._normalize_hex_string(str(row.get("request_payload") or ""))
            if payload_text != normalized_request:
                continue
            self.handled_generic_request_ids.add(str(request_id))
            return str(request_id)
        return None

    def _apply_hot_overrides(self, overrides: Dict[str, Any]) -> None:
        field_map = {
            "BMS_POLL_INTERVAL": "bms_interval",
        }
        for key, attr in field_map.items():
            if key not in overrides:
                continue
            try:
                setattr(self.config, attr, float(overrides[key]))
            except (TypeError, ValueError):
                self.logger.debug("Ignore override key=%s value=%r", key, overrides.get(key))

    def _current_cableway_status_snapshot(self) -> Dict[str, Any]:
        elapsed = time.monotonic() - self.start_monotonic
        return {
            "timestamp": self._now_iso(),
            "ts": self._now_ts(),
            "plc_host": "virtual-plc.local",
            "faults": {
                "gz_total_fault": 10.0 <= (elapsed % 70.0) <= 16.0,
                "gz_position_fault": 10.0 <= (elapsed % 70.0) <= 16.0,
            },
            "outputs": {
                "q_forward": self.motion_direction == "forward",
                "q_reverse": self.motion_direction == "reverse",
                "q_work_brake": self.motion_direction == "idle",
            },
            "active_faults": ["gz_total_fault"] if 10.0 <= (elapsed % 70.0) <= 16.0 else [],
        }

    def _publish_json(self, topic: str, payload: Dict[str, Any] | list[Dict[str, Any]]) -> None:
        if not self.connected_event.is_set():
            return
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result = self.client.publish(topic, body, qos=0, retain=False)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            self.logger.warning("MQTT publish failed topic=%s rc=%s", topic, result.rc)

    def _api_request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[requests.Response]:
        base = self.config.api_base_url.rstrip("/")
        url = f"{base}{path}"
        try:
            response = self.http.request(
                method=method.upper(),
                url=url,
                json=json_payload,
                params=params,
                timeout=5,
            )
            if response.status_code >= 400:
                self.logger.warning("API %s %s failed status=%s", method.upper(), path, response.status_code)
            return response
        except requests.RequestException as exc:
            self.logger.warning("API %s %s failed error=%s", method.upper(), path, exc)
            return None

    def _build_command_response(self, payload: bytes) -> bytes:
        if not payload:
            return bytes.fromhex("01 03 00 00")
        data = bytearray(payload[:6] or payload)
        if len(data) < 4:
            data.extend(b"\x00" * (4 - len(data)))
        data[-2:] = b"\x90\x00"
        return bytes(data)

    def _direction_from_control_code(self, command_code: int) -> str:
        if command_code in {201, 401}:
            return "forward"
        if command_code in {202, 402}:
            return "reverse"
        return "idle"

    @staticmethod
    def _normalize_hex_string(value: str) -> str:
        return "".join(ch for ch in str(value).upper() if ch in "0123456789ABCDEF")

    @staticmethod
    def _bytes_to_hex(data: bytes) -> str:
        return " ".join(f"{byte:02X}" for byte in data)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).astimezone().isoformat()

    @staticmethod
    def _now_ts() -> float:
        return datetime.now(timezone.utc).timestamp()

    @staticmethod
    def _build_wav_base64(duration_seconds: float) -> str:
        sample_rate = 16_000
        amplitude = 10_000
        total_frames = int(sample_rate * duration_seconds)
        frames = bytearray()
        for index in range(total_frames):
            value = int(amplitude * math.sin(2.0 * math.pi * 440.0 * index / sample_rate))
            frames.extend(struct.pack("<h", value))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(bytes(frames))
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    @staticmethod
    def _build_png_base64(label: str) -> str:
        image = Image.new("RGB", (8, 8), color=(24, 47, 84))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 7, 7), outline=(255, 211, 92), width=1)
        draw.point((3, 3), fill=(247, 193, 119))
        draw.point((4, 4), fill=(255, 255, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Virtual client for MQTT/API integration testing.")
    parser.add_argument("--mqtt-broker", default=os.getenv("MQTT_BROKER", "localhost"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument("--mqtt-username", default=os.getenv("MQTT_USERNAME", ""))
    parser.add_argument("--mqtt-password", default=os.getenv("MQTT_PASSWORD", ""))
    parser.add_argument("--mqtt-client-id", default=os.getenv("MQTT_CLIENT_ID", "virtual_gateway_01"))
    parser.add_argument("--api-base-url", default=os.getenv("VIRTUAL_CLIENT_API_BASE", "http://127.0.0.1:8000/api"))
    parser.add_argument("--location", default=os.getenv("VIRTUAL_CLIENT_LOCATION", "station-entry-a"))
    parser.add_argument("--schema-version", type=int, default=int(os.getenv("MESSAGE_SCHEMA_VERSION", "1")))
    parser.add_argument("--sensor-interval", type=float, default=2.0)
    parser.add_argument("--bms-interval", type=float, default=5.0)
    parser.add_argument("--rfid-interval", type=float, default=12.0)
    parser.add_argument("--cableway-interval", type=float, default=1.0)
    parser.add_argument("--hello-interval", type=float, default=30.0)
    parser.add_argument("--media-interval", type=float, default=15.0)
    parser.add_argument("--metal-interval", type=float, default=20.0)
    parser.add_argument("--command-lookup-window", type=int, default=20)
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--no-seed-sensor-configs", action="store_true")
    parser.add_argument("--no-http-seed", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> SimulatorConfig:
    return SimulatorConfig(
        mqtt_broker=args.mqtt_broker,
        mqtt_port=args.mqtt_port,
        mqtt_username=args.mqtt_username,
        mqtt_password=args.mqtt_password,
        mqtt_client_id=args.mqtt_client_id,
        api_base_url=args.api_base_url,
        location=args.location,
        schema_version=args.schema_version,
        sensor_interval=args.sensor_interval,
        bms_interval=args.bms_interval,
        rfid_interval=args.rfid_interval,
        cableway_interval=args.cableway_interval,
        hello_interval=args.hello_interval,
        media_interval=args.media_interval,
        metal_interval=args.metal_interval,
        command_lookup_window=max(1, args.command_lookup_window),
        duration_seconds=max(0.0, args.duration_seconds),
        seed_sensor_configs=not args.no_seed_sensor_configs,
        enable_http_seed=not args.no_http_seed,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    simulator = VirtualClientSimulator(config_from_args(args))
    simulator.run()


if __name__ == "__main__":
    main()
