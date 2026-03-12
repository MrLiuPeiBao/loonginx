#!/usr/bin/env python3
import ast
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from communication.cableway_plc import CablewayPLC, CablewayPLCConfig
from communication.mqtt_client import MQTTClient
from communication.plc_logging import get_plc_logger
from communication.rfid_reader import RFIDReader
from communication.serial_manager import SerialManager
from config import (
    BMS_CONFIG,
    MQTT_CONFIG,
    MQTT_TOPICS,
    PLC_CONFIG,
    RFID_SERIAL_CONFIG,
    SENSOR_CONFIGS,
    SERIAL_CONFIG,
    MESSAGE_SCHEMA_VERSION,
    SENSOR_POLL_DELAY,
    SENSOR_RETRY_DELAY,
    MAX_SENSOR_ATTEMPTS,
    LOOP_IDLE_DELAY,
    BMS_POLL_INTERVAL,
    MQTT_DEGRADE_ENTER_SECONDS,
    MQTT_DEGRADE_EXIT_SECONDS,
    MQTT_DEGRADE_FACTOR,
)
from sensors.sensor_factory import SensorFactory
from utils.degraded_state import update_degraded_state
from utils.message_envelope import with_payload_type
from utils.config_override import (
    CONFIG_UPDATED_AT_KEY,
    CONFIG_VERSION_KEY,
    get_config_updated_at,
    get_config_version,
    load_overrides,
    merge_overrides,
    save_overrides,
    split_hot_and_restart,
)

plc_logger = get_plc_logger(__name__)


class SensorGateway:
    def __init__(self):
        self._setup_logging()
        self.serial_manager = SerialManager(**SERIAL_CONFIG)
        self.mqtt_client = MQTTClient(MQTT_CONFIG)
        self.rfid_reader = RFIDReader(**RFID_SERIAL_CONFIG)
        self.sensors: Dict[str, Any] = {}
        self.bms_sensor: Optional[Any] = None
        self.cableway_plc: Optional[CablewayPLC] = None
        self.running = False
        self.sensor_poll_delay = SENSOR_POLL_DELAY  # seconds between sequential sensor polls
        self.sensor_retry_delay = SENSOR_RETRY_DELAY  # seconds between retries for same sensor
        self.max_sensor_attempts = MAX_SENSOR_ATTEMPTS  # retry budget for each sensor per batch
        self.loop_idle_delay = LOOP_IDLE_DELAY  # main loop sleep
        self.bms_poll_interval = BMS_POLL_INTERVAL  # seconds between BMS reads
        self._last_bms_read = 0.0
        self._degraded = False
        self._mqtt_disconnected_since: Optional[float] = None
        self._mqtt_connected_since: Optional[float] = None
        self._degrade_enter_seconds = float(MQTT_DEGRADE_ENTER_SECONDS)
        self._degrade_exit_seconds = float(MQTT_DEGRADE_EXIT_SECONDS)
        self._degrade_factor = max(1.0, float(MQTT_DEGRADE_FACTOR))
        self._init_cableway_plc()

    @staticmethod
    def _setup_logging() -> None:
        handlers = [logging.StreamHandler()]
        try:
            handlers.insert(0, logging.FileHandler('sensor_gateway.log', encoding='utf-8'))
        except OSError as exc:
            print(f"Failed to open log file sensor_gateway.log: {exc}")
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
            handlers=handlers,
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
        self.mqtt_client.subscribe(MQTT_TOPICS['config_update'], self.handle_config_update)
        if self.cableway_plc:
            self.mqtt_client.subscribe(
                MQTT_TOPICS['cableway_command_request'],
                self.handle_cableway_command,
            )
            plc_logger.info(
                "Cableway PLC MQTT subscribed topic=%s",
                MQTT_TOPICS['cableway_command_request'],
            )

    # MQTT handlers ------------------------------------------------------
    def handle_command(self, payload: bytes) -> None:
        command_bytes, request_id = self._parse_command_payload(payload)
        if not command_bytes:
            logging.error("Unable to parse MQTT command payload=%s", payload)
            message = {
                'timestamp': self._get_timestamp(),
                'ts': self._get_ts(),
                'schema': MESSAGE_SCHEMA_VERSION,
                'success': False,
                'error': 'invalid command payload',
            }
            with_payload_type(message, 'command_response')
            if request_id:
                message['request_id'] = request_id
            self.mqtt_client.publish(MQTT_TOPICS['command_response'], message)
            return

        response = self.serial_manager.send_raw_command(command_bytes)

        message = {
            'timestamp': self._get_timestamp(),
            'ts': self._get_ts(),
            'schema': MESSAGE_SCHEMA_VERSION,
            'request_hex': self._format_hex(command_bytes),
            'success': bool(response),
        }
        with_payload_type(message, 'command_response')
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

    def handle_cableway_command(self, payload: bytes) -> None:
        request_id: Optional[str] = None
        command_type = None
        response: Dict[str, Any] = {
            'timestamp': self._get_timestamp(),
            'ts': self._get_ts(),
            'schema': MESSAGE_SCHEMA_VERSION,
            'success': False,
        }
        with_payload_type(response, 'cableway_command_response')

        try:
            plc_logger.debug(
                "Cableway command received bytes=%s",
                len(payload) if payload is not None else 0,
            )
            text = payload.decode('utf-8', errors='ignore').strip()
            data = json.loads(text) if text else {}
            if not isinstance(data, dict):
                raise ValueError('payload must be a JSON object')

            target_device = data.get('device_id')
            gateway_device = MQTT_CONFIG.get('client_id')
            if target_device and gateway_device and str(target_device) != str(gateway_device):
                plc_logger.info(
                    "Ignore cableway command for other device_id=%s (self=%s)",
                    target_device,
                    gateway_device,
                )
                return

            request_id = data.get('request_id') or data.get('id')
            command_type = data.get('type') or data.get('action')
            pulse = data.get('pulse')
            pulse_enabled = True if pulse is None else bool(pulse)
            plc_logger.debug(
                "Cableway command parsed type=%s request_id=%s pulse=%s",
                command_type,
                request_id,
                pulse_enabled,
            )

            if not self.cableway_plc:
                raise RuntimeError('cableway plc module not enabled')

            result: Optional[dict] = None
            if command_type == 'control':
                code = int(data.get('command_code'))
                plc_logger.info(
                    "Cableway command execute control code=%s request_id=%s",
                    code,
                    request_id,
                )
                self.cableway_plc.send_control_command(code, pulse=pulse_enabled)
            elif command_type == 'estop':
                plc_logger.info(
                    "Cableway command execute estop request_id=%s",
                    request_id,
                )
                self.cableway_plc.send_estop(pulse=pulse_enabled)
            elif command_type == 'set_params':
                params = data.get('params')
                if not isinstance(params, dict) or not params:
                    raise ValueError('params must be a non-empty object')
                cast_params = {str(k): float(v) for k, v in params.items()}
                plc_logger.info(
                    "Cableway command execute set_params count=%s request_id=%s",
                    len(cast_params),
                    request_id,
                )
                self.cableway_plc.set_params(cast_params)
            elif command_type == 'read_status':
                plc_logger.info(
                    "Cableway command execute read_status request_id=%s",
                    request_id,
                )
                result = self.cableway_plc.read_status()
            else:
                raise ValueError(f'unsupported command type={command_type}')

            response['success'] = True
            response['type'] = command_type
            if result is not None:
                response['result'] = result
            plc_logger.info(
                "Cableway command success type=%s request_id=%s",
                command_type,
                request_id,
            )
        except Exception as exc:
            response['success'] = False
            response['type'] = command_type or 'unknown'
            response['error'] = str(exc)
            plc_logger.exception(
                "Cableway command failed type=%s request_id=%s error=%s",
                command_type,
                request_id,
                exc,
            )

        if request_id:
            response['request_id'] = request_id
        response['device_id'] = MQTT_CONFIG.get('client_id')
        self.mqtt_client.publish(MQTT_TOPICS['cableway_command_response'], response)

    def handle_config_update(self, payload: bytes) -> None:
        response: Dict[str, Any] = {
            'timestamp': self._get_timestamp(),
            'ts': self._get_ts(),
            'schema': MESSAGE_SCHEMA_VERSION,
            'success': False,
            'device_id': MQTT_CONFIG.get('client_id'),
        }
        with_payload_type(response, 'config_ack')
        try:
            text = payload.decode('utf-8', errors='ignore').strip()
            data = json.loads(text) if text else {}
            if not isinstance(data, dict):
                raise ValueError('payload must be a JSON object')
            overrides = data.get('payload') or {}
            if not isinstance(overrides, dict):
                raise ValueError('payload field must be a JSON object')

            hot, pending = split_hot_and_restart(overrides)
            version = data.get('version')
            applied_keys = self._apply_hot_overrides(hot)
            pending_keys = list(pending.keys())
            if pending or version is not None:
                current = load_overrides()
                if pending:
                    current = merge_overrides(current, pending)
                if version is not None:
                    try:
                        current[CONFIG_VERSION_KEY] = int(version)
                    except (TypeError, ValueError):
                        current[CONFIG_VERSION_KEY] = 0
                    current[CONFIG_UPDATED_AT_KEY] = time.time()
                save_overrides(current)
            response['success'] = True
            if applied_keys:
                response['applied_keys'] = applied_keys
            if pending_keys:
                response['pending_restart_keys'] = pending_keys
        except Exception as exc:
            logging.error("Config update failed: %s", exc)
            response['error'] = str(exc)
        self.mqtt_client.publish(MQTT_TOPICS['config_ack'], response)

    def _apply_hot_overrides(self, overrides: Dict[str, Any]) -> list[str]:
        applied: list[str] = []

        def _apply_float(key: str, attr: str) -> None:
            if key not in overrides:
                return
            try:
                value = float(overrides[key])
            except (TypeError, ValueError):
                return
            setattr(self, attr, value)
            applied.append(key)

        def _apply_int(key: str, attr: str) -> None:
            if key not in overrides:
                return
            try:
                value = int(overrides[key])
            except (TypeError, ValueError):
                return
            setattr(self, attr, value)
            applied.append(key)

        _apply_float('SENSOR_POLL_DELAY', 'sensor_poll_delay')
        _apply_float('SENSOR_RETRY_DELAY', 'sensor_retry_delay')
        _apply_int('MAX_SENSOR_ATTEMPTS', 'max_sensor_attempts')
        _apply_float('LOOP_IDLE_DELAY', 'loop_idle_delay')
        _apply_float('BMS_POLL_INTERVAL', 'bms_poll_interval')
        _apply_float('MQTT_DEGRADE_ENTER_SECONDS', '_degrade_enter_seconds')
        _apply_float('MQTT_DEGRADE_EXIT_SECONDS', '_degrade_exit_seconds')
        _apply_float('MQTT_DEGRADE_FACTOR', '_degrade_factor')

        mqtt_keys = [key for key in overrides.keys() if key.startswith('MQTT_')]
        if mqtt_keys:
            self.mqtt_client.apply_runtime_config(overrides)
            applied.extend([key for key in mqtt_keys if key not in applied])

        return applied
        plc_logger.debug(
            "Cableway command response published success=%s request_id=%s",
            response.get('success'),
            request_id,
        )

    def handle_rfid_data(self, rfid_data: dict) -> None:
        rfid_data.setdefault('schema', MESSAGE_SCHEMA_VERSION)
        with_payload_type(rfid_data, 'rfid_data')
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
        return datetime.now().astimezone().isoformat()

    @staticmethod
    def _get_ts() -> float:
        return datetime.now().astimezone().timestamp()

    def _update_degraded_state(self, now: float) -> None:
        self._degraded, self._mqtt_disconnected_since, self._mqtt_connected_since = update_degraded_state(
            connected=bool(self.mqtt_client.connected),
            now=now,
            degraded=self._degraded,
            disconnected_since=self._mqtt_disconnected_since,
            connected_since=self._mqtt_connected_since,
            enter_after=self._degrade_enter_seconds,
            exit_after=self._degrade_exit_seconds,
        )

    # Sensor loop --------------------------------------------------------
    async def read_sensors_loop(self) -> None:
        while self.running:
            try:
                self._update_degraded_state(time.monotonic())
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

                    formatted.setdefault('schema', MESSAGE_SCHEMA_VERSION)
                    with_payload_type(formatted, 'sensor_data')
                    sensor_payload.append(formatted)
                    factor = self._degrade_factor if self._degraded else 1.0
                    await asyncio.sleep(self.sensor_poll_delay * factor)

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
                        bms_data.setdefault('schema', MESSAGE_SCHEMA_VERSION)
                        with_payload_type(bms_data, 'bms_data')
                        self.mqtt_client.publish(MQTT_TOPICS['bms_data'], bms_data)
                    self._last_bms_read = now
            except Exception as exc:
                logging.error("Failed to read sensors: %s", exc)

            factor = self._degrade_factor if self._degraded else 1.0
            await asyncio.sleep(self.loop_idle_delay * factor)

    async def read_cableway_plc_loop(self) -> None:
        if not self.cableway_plc:
            return
        poll_interval = max(0.1, float(self.cableway_plc.status_poll_interval))
        plc_logger.info("Cableway PLC loop started interval=%s", poll_interval)
        while self.running:
            try:
                self.mqtt_client.ensure_connected()
                status = await asyncio.to_thread(self.cableway_plc.poll_status_and_heartbeat)
                if status:
                    status['device_id'] = MQTT_CONFIG.get('client_id')
                    status.setdefault('location', 'unknown')
                    status.setdefault('schema', MESSAGE_SCHEMA_VERSION)
                    with_payload_type(status, 'cableway_status')
                    self.mqtt_client.publish(MQTT_TOPICS['cableway_status'], status)
                    plc_logger.debug(
                        "Cableway PLC status published active_faults=%s",
                        status.get('active_faults'),
                    )
            except Exception as exc:
                plc_logger.exception("Cableway PLC loop error: %s", exc)
            await asyncio.sleep(poll_interval)

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

    async def _run_tasks(self) -> None:
        tasks = [asyncio.create_task(self.read_sensors_loop())]
        if self.cableway_plc:
            tasks.append(asyncio.create_task(self.read_cableway_plc_loop()))
        await asyncio.gather(*tasks)

    # Lifecycle ----------------------------------------------------------
    def start(self) -> None:
        try:
            logging.info("Starting sensor gateway...")
            self.running = True

            self.mqtt_client.connect()
            time.sleep(2)

            self.setup_mqtt_callbacks()
            self.send_device_hello()
            self.initialize_sensors()
            self.rfid_reader.start_reading(self.handle_rfid_data)

            asyncio.run(self._run_tasks())
        except KeyboardInterrupt:
            logging.info("Stop signal received")
        except Exception as exc:
            logging.error("Gateway runtime error: %s", exc)
        finally:
            self.stop()

    def _init_cableway_plc(self) -> None:
        try:
            if not isinstance(PLC_CONFIG, dict) or not PLC_CONFIG.get('enabled'):
                plc_logger.info("Cableway PLC disabled by config")
                return
            self.cableway_plc = CablewayPLC(
                CablewayPLCConfig(
                    host=str(PLC_CONFIG.get('host', '192.168.2.1')),
                    port=int(PLC_CONFIG.get('port', 502)),
                    unit_id=int(PLC_CONFIG.get('unit_id', 1)),
                    timeout=float(PLC_CONFIG.get('timeout', 2.0)),
                    connect_timeout=float(PLC_CONFIG.get('connect_timeout', 2.0)),
                    status_poll_interval=float(PLC_CONFIG.get('status_poll_interval', 0.5)),
                    heartbeat_interval=float(PLC_CONFIG.get('heartbeat_interval', 1.0)),
                    command_pulse_seconds=float(PLC_CONFIG.get('command_pulse_seconds', 0.1)),
                    even_byte_is_high=bool(PLC_CONFIG.get('even_byte_is_high', True)),
                    float_word_order=str(PLC_CONFIG.get('float_word_order', 'big')),
                    float_byte_order=str(PLC_CONFIG.get('float_byte_order', 'big')),
                )
            )
            plc_logger.info(
                "Cableway PLC enabled host=%s port=%s unit_id=%s",
                PLC_CONFIG.get('host'),
                PLC_CONFIG.get('port'),
                PLC_CONFIG.get('unit_id'),
            )
        except Exception as exc:
            plc_logger.exception("Failed to init cableway PLC module: %s", exc)
            self.cableway_plc = None

    def stop(self) -> None:
        if not self.running:
            logging.info("Sensor gateway already stopped")
            return

        logging.info("Stopping sensor gateway...")
        self.running = False
        self.rfid_reader.stop_reading()
        if self.cableway_plc:
            try:
                plc_logger.info("Cableway PLC close start")
                self.cableway_plc.close()
            except Exception:
                plc_logger.debug("Cableway PLC close failed", exc_info=True)
        self.serial_manager.close_all()
        self.mqtt_client.disconnect()

    def send_device_hello(self) -> None:
        payload: Dict[str, Any] = {
            'config_version': get_config_version(),
            'last_ts': get_config_updated_at(),
            'device_id': MQTT_CONFIG.get('client_id'),
            'timestamp': self._get_timestamp(),
            'ts': self._get_ts(),
            'schema': MESSAGE_SCHEMA_VERSION,
        }
        with_payload_type(payload, 'device_hello')
        self.mqtt_client.publish(MQTT_TOPICS['device_hello'], payload)


if __name__ == "__main__":
    SensorGateway().start()
