#!/usr/bin/env python3
import ast
import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from communication.mqtt_client import MQTTClient
from communication.rfid_reader import RFIDReader
from communication.serial_manager import SerialManager
from config import (
    BMS_CONFIG,
    MQTT_CONFIG,
    MQTT_TOPICS,
    RFID_SERIAL_CONFIG,
    SENSOR_CONFIGS,
    SERIAL_CONFIG,
)
from sensors.sensor_factory import SensorFactory


class SensorGateway:
    def __init__(self):
        self._setup_logging()
        self.serial_manager = SerialManager(**SERIAL_CONFIG)
        self.mqtt_client = MQTTClient(MQTT_CONFIG)
        self.rfid_reader = RFIDReader(**RFID_SERIAL_CONFIG)
        self.sensors: Dict[str, Any] = {}
        self.bms_sensor: Optional[Any] = None
        self.running = False
        self.sensor_poll_delay = 0.08  # seconds between sequential sensor polls
        self.sensor_retry_delay = 0.08  # seconds between retries for same sensor
        self.max_sensor_attempts = 3    # retry budget for each sensor per batch
        self.loop_idle_delay = 0.5     # main loop sleep
        self.bms_poll_interval = 1.0   # seconds between BMS reads
        self._last_bms_read = 0.0

    @staticmethod
    def _setup_logging() -> None:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
            handlers=[
                logging.FileHandler('sensor_gateway.log', encoding='utf-8'),
                logging.StreamHandler(),
            ],
        )

    # Initialisation -----------------------------------------------------
    def initialize_sensors(self) -> None:
        try:
            for sensor_type, cfg in SENSOR_CONFIGS.items():
                sensor = SensorFactory.create_sensor(sensor_type, self.serial_manager, cfg)
                if sensor:
                    self.sensors[sensor_type] = sensor
                    logging.info(
                        "Sensor ready type=%s address=%s",
                        sensor_type,
                        sensor.address,
                    )

            self.bms_sensor = SensorFactory.create_sensor('bms', self.serial_manager, BMS_CONFIG)
            if self.bms_sensor:
                logging.info("BMS sensor ready address=%s", self.bms_sensor.address)
        except Exception as exc:
            logging.error("Failed to initialise sensors: %s", exc)

    def setup_mqtt_callbacks(self) -> None:
        self.mqtt_client.subscribe(MQTT_TOPICS['command_request'], self.handle_command)

    # MQTT handlers ------------------------------------------------------
    def handle_command(self, payload: bytes) -> None:
        command_bytes, request_id = self._parse_command_payload(payload)
        if not command_bytes:
            logging.error("Unable to parse MQTT command payload=%s", payload)
            message = {
                'timestamp': self._get_timestamp(),
                'success': False,
                'error': 'invalid command payload',
            }
            if request_id:
                message['request_id'] = request_id
            self.mqtt_client.publish(MQTT_TOPICS['command_response'], message)
            return

        response = self.serial_manager.send_raw_command(command_bytes)

        message = {
            'timestamp': self._get_timestamp(),
            'request_hex': self._format_hex(command_bytes),
            'success': bool(response),
        }
        if request_id:
            message['request_id'] = request_id

        if response:
            message['response_hex'] = self._format_hex(response)
            logging.info(
                "Command executed request=%s response=%s",
                message['request_hex'],
                message['response_hex'],
            )
        else:
            message['error'] = 'no response or CRC failure'
            logging.error("Command execution failed request=%s", message['request_hex'])

        self.mqtt_client.publish(MQTT_TOPICS['command_response'], message)

    def handle_rfid_data(self, rfid_data: dict) -> None:
        self.mqtt_client.publish(MQTT_TOPICS['rfid_data'], rfid_data)
        logging.info("RFID report card_id=%s", rfid_data.get('card_id'))

    # Command parsing ----------------------------------------------------
    def _parse_command_payload(self, payload: Any) -> tuple[Optional[bytes], Optional[str]]:
        request_id: Optional[str] = None
        if isinstance(payload, (bytes, bytearray, memoryview)):
            raw = bytes(payload)
            # Raw Modbus frames usually contain non-printable bytes; treat them as-is.
            if any(b < 32 for b in raw):
                return raw, request_id
            text = raw.decode('utf-8', errors='ignore').strip()
            if not text:
                return raw, request_id
            parsed, request_id = self._parse_command_text(text)
            return (parsed if parsed else raw), request_id

        if isinstance(payload, str):
            parsed, request_id = self._parse_command_text(payload.strip())
            return parsed, request_id

        return None, request_id

    def _parse_command_text(self, text: str) -> tuple[Optional[bytes], Optional[str]]:
        if not text:
            return None, None

        request_id: Optional[str] = None
        # Try JSON first to allow request_id + command payload envelopes.
        try:
            import json

            parsed_json = json.loads(text)
            if isinstance(parsed_json, dict):
                request_id = parsed_json.get('request_id') or parsed_json.get('id')
                command_field = (
                    parsed_json.get('command')
                    or parsed_json.get('payload')
                    or parsed_json.get('request_hex')
                    or parsed_json.get('data')
                )
                if command_field is not None:
                    inner_text = command_field if isinstance(command_field, str) else str(command_field)
                    parsed_bytes, _ = self._parse_command_text(inner_text)
                    return parsed_bytes, request_id
                if isinstance(parsed_json.get('bytes'), list):
                    try:
                        bytes_list = [int(item) & 0xFF for item in parsed_json['bytes']]
                        return bytes(bytes_list), request_id
                    except Exception:
                        pass
            elif isinstance(parsed_json, list):
                try:
                    values = [int(item) & 0xFF for item in parsed_json]
                    return bytes(values), request_id
                except Exception:
                    pass
        except Exception:
            pass

        if not text:
            return None, request_id

        try:
            if text.startswith('[') and text.endswith(']'):
                values = ast.literal_eval(text)
                bytes_list = []
                for value in values:
                    if isinstance(value, str):
                        bytes_list.append(int(value, 0) & 0xFF)
                    else:
                        bytes_list.append(int(value) & 0xFF)
                return bytes(bytes_list), request_id
        except (SyntaxError, ValueError, TypeError):
            pass

        cleaned = text.replace(',', ' ').replace('0x', '').replace('0X', '')
        tokens = [token for token in cleaned.split() if token]
        if tokens:
            try:
                values = []
                for token in tokens:
                    try:
                        value = int(token, 16) if len(token) == 2 else int(token, 0)
                    except ValueError:
                        value = int(token, 16)
                    values.append(value & 0xFF)
                return bytes(values), request_id
            except ValueError:
                pass

        stripped = ''.join(text.split())
        if len(stripped) % 2 == 0:
            try:
                return bytes.fromhex(stripped), request_id
            except ValueError:
                return None, request_id

        return None, request_id

    @staticmethod
    def _format_hex(data: bytes) -> str:
        return ' '.join(f'{byte:02X}' for byte in data)

    @staticmethod
    def _get_timestamp() -> str:
        return datetime.now().isoformat()

    # Sensor loop --------------------------------------------------------
    async def read_sensors_loop(self) -> None:
        while self.running:
            try:
                self.mqtt_client.ensure_connected()
                sensor_payload = []
                failed_sensors = []

                for sensor in self.sensors.values():
                    formatted = await self._read_sensor_with_retries(sensor)
                    if formatted is None:
                        failed_sensors.append(
                            getattr(sensor, 'name', sensor.__class__.__name__),
                        )
                        continue

                    sensor_payload.append(formatted)
                    await asyncio.sleep(self.sensor_poll_delay)

                if sensor_payload:
                    self.mqtt_client.publish(MQTT_TOPICS['sensor_data'], sensor_payload)
                if failed_sensors:
                    logging.warning(
                        "Sensors skipped in batch: %s",
                        ", ".join(failed_sensors),
                    )

                now = time.monotonic()
                if self.bms_sensor and now - self._last_bms_read >= self.bms_poll_interval:
                    bms_data = await asyncio.to_thread(self.bms_sensor.read_all_data)
                    if bms_data:
                        self.mqtt_client.publish(MQTT_TOPICS['bms_data'], bms_data)
                    self._last_bms_read = now
            except Exception as exc:
                logging.error("Failed to read sensors: %s", exc)

            await asyncio.sleep(self.loop_idle_delay)

    async def _read_sensor_with_retries(self, sensor: Any) -> Optional[dict]:
        sensor_name = getattr(sensor, 'name', sensor.__class__.__name__)
        sensor_address = getattr(sensor, 'address', 'unknown')

        for attempt in range(1, self.max_sensor_attempts + 1):
            try:
                formatted = await asyncio.to_thread(sensor.get_formatted_data)
            except Exception as sensor_exc:
                logging.error(
                    "Sensor read exception type=%s address=%s attempt=%s/%s error=%s",
                    sensor_name,
                    sensor_address,
                    attempt,
                    self.max_sensor_attempts,
                    sensor_exc,
                )
                formatted = None

            if formatted:
                return formatted

            logging.warning(
                "Sensor read missing type=%s address=%s attempt=%s/%s",
                sensor_name,
                sensor_address,
                attempt,
                self.max_sensor_attempts,
            )

            if attempt < self.max_sensor_attempts:
                await asyncio.sleep(self.sensor_retry_delay)

        return None

    # Lifecycle ----------------------------------------------------------
    def start(self) -> None:
        try:
            logging.info("Starting sensor gateway...")
            self.running = True

            self.mqtt_client.connect()
            time.sleep(2)

            self.setup_mqtt_callbacks()
            self.initialize_sensors()
            self.rfid_reader.start_reading(self.handle_rfid_data)

            asyncio.run(self.read_sensors_loop())
        except KeyboardInterrupt:
            logging.info("Stop signal received")
        except Exception as exc:
            logging.error("Gateway runtime error: %s", exc)
        finally:
            self.stop()

    def stop(self) -> None:
        if not self.running:
            logging.info("Sensor gateway already stopped")
            return

        logging.info("Stopping sensor gateway...")
        self.running = False
        self.rfid_reader.stop_reading()
        self.serial_manager.close_all()
        self.mqtt_client.disconnect()


if __name__ == "__main__":
    SensorGateway().start()
