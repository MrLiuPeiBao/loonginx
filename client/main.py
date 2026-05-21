#!/usr/bin/env python3
import ast
import asyncio
import json
import logging
import os
import signal
import socket
import time
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from communication.io_board import IOBoard
from communication.mqtt_client import MQTTClient
from communication.rfid_reader import RFIDReader
from communication.serial_manager import SerialManager
from config import (
    BMS_CONFIG,
    CAMERA_HOST,
    CAMERA_PORT,
    CAMERA_TARGET,
    CAMERA_TIMEOUT,
    ALARM_OUTPUT_HOLD_SECONDS,
    IO_BOARD_CONFIG,
    LOCAL_SENSOR_THRESHOLDS,
    MQTT_CONFIG,
    MQTT_TOPICS,
    RFID_SERIAL_CONFIG,
    SENSOR_CONFIGS,
    SERIAL_CONFIG,
    MESSAGE_SCHEMA_VERSION,
    SENSOR_POLL_DELAY,
    SENSOR_RETRY_DELAY,
    MAX_SENSOR_ATTEMPTS,
    LOOP_IDLE_DELAY,
    BMS_POLL_INTERVAL,
    ENABLED_SENSOR_TYPES,
    MQTT_DEGRADE_ENTER_SECONDS,
    MQTT_DEGRADE_EXIT_SECONDS,
    MQTT_DEGRADE_FACTOR,
    SENSOR_FAILURE_BACKOFF_SECONDS,
    SENSOR_READ_HARD_TIMEOUT,
    BMS_READ_HARD_TIMEOUT,
    OUTPUT_ACTIONS_PER_CYCLE,
    OUTPUT_RETRY_DELAY,
    OUTPUT_RESPONSE_TIMEOUT,
    OUTPUT_SETTLE_DELAY,
    PHOTOELECTRIC_POLL_INTERVAL,
    OBSTACLE_OUTPUT_DELAY,
    OUTPUT_STARTUP_HOLDOFF_SECONDS,
    OUTPUT_IDLE_GAP_SECONDS,
    ENV_SENSOR_GROUP_INTERVAL,
    GAS_SENSOR_GROUP_INTERVAL,
    BMS_GROUP_INTERVAL,
    BMS_FAST_GROUP_INTERVAL,
    BMS_SLOW_GROUP_INTERVAL,
    ENV_SENSOR_GROUP_PHASE_OFFSET,
    GAS_SENSOR_GROUP_PHASE_OFFSET,
    PHOTOELECTRIC_GROUP_PHASE_OFFSET,
    BMS_FAST_GROUP_PHASE_OFFSET,
    BMS_SLOW_GROUP_PHASE_OFFSET,
    GATEWAY_HEARTBEAT_SECONDS,
    GATEWAY_WATCHDOG_ENABLED,
    GATEWAY_WATCHDOG_TIMEOUT,
    GATEWAY_EXIT_ON_BLOCKING_TIMEOUT,
    OUTPUT_MAX_RETRIES,
    IO_DEGRADED_HOLDOFF_SECONDS,
    PHOTOELECTRIC_AFTER_OUTPUT_GAP_SECONDS,
    STARTUP_IO_OUTPUTS_ENABLED,
    CAMERA_LIGHT_OUTPUT_ENABLED,
    ADDRESS_SUSPECT_FAILURES,
    ADDRESS_DEGRADED_FAILURES,
    ADDRESS_RECOVERY_SUCCESSES,
    ADDRESS_SUSPECT_BACKOFF_SECONDS,
    ADDRESS_DEGRADED_BACKOFF_SECONDS,
    SERIAL_STALL_HOLDOFF_SECONDS,
    SENSOR_STALE_MAX_AGE_SECONDS,
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

DANGEROUS_GAS_SENSOR_TYPES = frozenset({'co', 'h2s', 'o2', 'ch4', 'smoke'})
APP_DIR = Path(__file__).resolve().parent
GATEWAY_LOG_PATH = APP_DIR / 'sensor_gateway.log'


class SensorGateway:
    def __init__(self):
        self._setup_logging()
        self.serial_manager = SerialManager(**SERIAL_CONFIG)
        self.mqtt_client = MQTTClient(MQTT_CONFIG)
        self.io_board = IOBoard(self.serial_manager, IO_BOARD_CONFIG)
        self.rfid_reader = RFIDReader(**RFID_SERIAL_CONFIG)
        self.sensors: Dict[str, Any] = {}
        self.bms_sensor: Optional[Any] = None
        self.running = False
        self.sensor_poll_delay = SENSOR_POLL_DELAY  # seconds between sequential sensor polls
        self.sensor_retry_delay = SENSOR_RETRY_DELAY  # seconds between retries for same sensor
        self.max_sensor_attempts = MAX_SENSOR_ATTEMPTS  # retry budget for each sensor per batch
        self.loop_idle_delay = LOOP_IDLE_DELAY  # main loop sleep
        self.bms_poll_interval = BMS_POLL_INTERVAL  # seconds between BMS reads
        self.sensor_read_hard_timeout = max(0.5, float(SENSOR_READ_HARD_TIMEOUT))
        self.bms_read_hard_timeout = max(1.0, float(BMS_READ_HARD_TIMEOUT))
        self.output_actions_per_cycle = max(0, int(OUTPUT_ACTIONS_PER_CYCLE))
        self.output_retry_delay = max(0.0, float(OUTPUT_RETRY_DELAY))
        self.output_response_timeout = max(0.05, float(OUTPUT_RESPONSE_TIMEOUT))
        self.output_settle_delay = max(0.0, float(OUTPUT_SETTLE_DELAY))
        self.output_max_retries = max(0, int(OUTPUT_MAX_RETRIES))
        self.photoelectric_poll_interval = max(0.5, float(PHOTOELECTRIC_POLL_INTERVAL))
        self.obstacle_output_delay = max(0.0, float(OBSTACLE_OUTPUT_DELAY))
        self.output_startup_holdoff_seconds = max(0.0, float(OUTPUT_STARTUP_HOLDOFF_SECONDS))
        self.output_idle_gap_seconds = max(0.0, float(OUTPUT_IDLE_GAP_SECONDS))
        self.io_degraded_holdoff_seconds = max(1.0, float(IO_DEGRADED_HOLDOFF_SECONDS))
        self.photoelectric_after_output_gap_seconds = max(
            0.0,
            float(PHOTOELECTRIC_AFTER_OUTPUT_GAP_SECONDS),
        )
        self.env_sensor_group_interval = max(0.5, float(ENV_SENSOR_GROUP_INTERVAL))
        self.gas_sensor_group_interval = max(0.5, float(GAS_SENSOR_GROUP_INTERVAL))
        self.bms_group_interval = max(0.5, float(BMS_GROUP_INTERVAL))
        self.bms_fast_group_interval = max(0.5, float(BMS_FAST_GROUP_INTERVAL))
        self.bms_slow_group_interval = max(0.5, float(BMS_SLOW_GROUP_INTERVAL))
        self.group_phase_offsets = {
            'env': max(0.0, float(ENV_SENSOR_GROUP_PHASE_OFFSET)),
            'gas': max(0.0, float(GAS_SENSOR_GROUP_PHASE_OFFSET)),
            'photoelectric': max(0.0, float(PHOTOELECTRIC_GROUP_PHASE_OFFSET)),
            'bms_fast': max(0.0, float(BMS_FAST_GROUP_PHASE_OFFSET)),
            'bms_slow': max(0.0, float(BMS_SLOW_GROUP_PHASE_OFFSET)),
        }
        self.gateway_heartbeat_seconds = max(5.0, float(GATEWAY_HEARTBEAT_SECONDS))
        self.gateway_watchdog_enabled = bool(GATEWAY_WATCHDOG_ENABLED)
        self.gateway_watchdog_timeout = max(
            self.gateway_heartbeat_seconds * 2.0,
            float(GATEWAY_WATCHDOG_TIMEOUT),
        )
        self.gateway_exit_on_blocking_timeout = bool(GATEWAY_EXIT_ON_BLOCKING_TIMEOUT)
        self.address_suspect_failures = max(1, int(ADDRESS_SUSPECT_FAILURES))
        self.address_degraded_failures = max(
            self.address_suspect_failures,
            int(ADDRESS_DEGRADED_FAILURES),
        )
        self.address_recovery_successes = max(1, int(ADDRESS_RECOVERY_SUCCESSES))
        self.address_suspect_backoff_seconds = max(0.0, float(ADDRESS_SUSPECT_BACKOFF_SECONDS))
        self.address_degraded_backoff_seconds = max(
            self.address_suspect_backoff_seconds,
            float(ADDRESS_DEGRADED_BACKOFF_SECONDS),
        )
        self.serial_stall_holdoff_seconds = max(0.0, float(SERIAL_STALL_HOLDOFF_SECONDS))
        self.sensor_stale_max_age_seconds = max(0.0, float(SENSOR_STALE_MAX_AGE_SECONDS))
        self.enabled_sensor_types = {
            str(sensor_type).strip().lower()
            for sensor_type in ENABLED_SENSOR_TYPES
            if str(sensor_type).strip()
        }
        self.sensor_failure_backoff_seconds = max(0.0, float(SENSOR_FAILURE_BACKOFF_SECONDS))
        self._last_bms_read = 0.0
        self._degraded = False
        self._mqtt_disconnected_since: Optional[float] = None
        self._mqtt_connected_since: Optional[float] = None
        self._degrade_enter_seconds = float(MQTT_DEGRADE_ENTER_SECONDS)
        self._degrade_exit_seconds = float(MQTT_DEGRADE_EXIT_SECONDS)
        self._degrade_factor = max(1.0, float(MQTT_DEGRADE_FACTOR))
        self.local_sensor_thresholds = dict(LOCAL_SENSOR_THRESHOLDS)
        self.alarm_output_hold_seconds = float(ALARM_OUTPUT_HOLD_SECONDS)
        self._alarm_active = False
        self._alarm_hold_until = 0.0
        self._obstacle_active = False
        self._camera_checked = False
        self._config_aligned = False
        self._hello_attempts = 0
        self._startup_outputs_thread: Optional[threading.Thread] = None
        self._sensor_backoff_until: Dict[str, float] = {}
        self._address_health: Dict[int, Dict[str, Any]] = {}
        self._last_loop_heartbeat = 0.0
        self._last_loop_progress = time.monotonic()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._pending_output_actions: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._last_photoelectric_read = 0.0
        self._started_at = time.monotonic()
        self._last_bus_activity = 0.0
        self._last_output_activity = 0.0
        self._io_degraded_until = 0.0
        self._io_degraded_reason: Optional[str] = None
        self._transaction_metrics: Dict[str, Any] = {
            'counts': {},
            'durations_ms': {},
            'last': {},
        }
        self._snapshot_cache: Dict[str, Dict[str, dict]] = {
            'env': {},
            'gas': {},
            'bms': {},
        }
        self._snapshot_last_publish_ts: Dict[str, float] = {
            'env': 0.0,
            'gas': 0.0,
            'bms': 0.0,
        }
        self._last_group_poll: Dict[str, float] = {
            'env': 0.0,
            'gas': 0.0,
            'photoelectric': 0.0,
            'bms_fast': 0.0,
            'bms_slow': 0.0,
        }
        self._group_cursor = 0
        self._group_sensor_cursor: Dict[str, int] = {
            'env': 0,
            'gas': 0,
            'photoelectric': 0,
        }
        self.serial_manager.set_transaction_observer(self._record_transaction_metric)
        self._install_signal_handlers()

    @staticmethod
    def _setup_logging() -> None:
        handlers = [logging.StreamHandler()]
        try:
            handlers.insert(0, logging.FileHandler(GATEWAY_LOG_PATH, encoding='utf-8'))
        except OSError as exc:
            print(f"Failed to open log file {GATEWAY_LOG_PATH}: {exc}")
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
            handlers=handlers,
        )

    def _install_signal_handlers(self) -> None:
        for signum in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
            if signum is None:
                continue
            try:
                signal.signal(signum, self._handle_signal)
            except Exception:
                continue

    def _handle_signal(self, signum, _frame) -> None:
        logging.info("Signal received signum=%s, stopping gateway", signum)
        self.stop()

    def _preflight_release_serial_port(self) -> None:
        try:
            self.serial_manager.close_all()
        except Exception as exc:
            logging.warning("Preflight serial release failed error=%s", exc)

    # Initialisation -----------------------------------------------------
    def initialize_sensors(self) -> None:
        try:
            self.bms_sensor = None
            for sensor_type, cfg in SENSOR_CONFIGS.items():
                if self.enabled_sensor_types and sensor_type not in self.enabled_sensor_types:
                    logging.info("Sensor disabled by config type=%s", sensor_type)
                    continue
                sensor = SensorFactory.create_sensor(sensor_type, self.serial_manager, cfg)
                if sensor:
                    self.sensors[sensor_type] = sensor
                    logging.info(
                        "Sensor ready type=%s address=%s",
                        sensor_type,
                        sensor.address,
                    )

            if not self.enabled_sensor_types or 'bms' in self.enabled_sensor_types:
                self.bms_sensor = SensorFactory.create_sensor('bms', self.serial_manager, BMS_CONFIG)
                if self.bms_sensor:
                    logging.info("BMS sensor ready address=%s", self.bms_sensor.address)
            else:
                logging.info("Sensor disabled by config type=bms")
        except Exception as exc:
            logging.error("Failed to initialise sensors: %s", exc)

    def setup_mqtt_callbacks(self) -> None:
        self.mqtt_client.subscribe(MQTT_TOPICS['command_request'], self.handle_command)
        self.mqtt_client.subscribe(MQTT_TOPICS['config_update'], self.handle_config_update)
        self.mqtt_client.subscribe(MQTT_TOPICS['alarm_broadcast'], self.handle_alarm_broadcast)

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
            response['status'] = 'ok'
        except Exception as exc:
            logging.error("Config update failed: %s", exc)
            response['error'] = str(exc)
            response['status'] = 'error'
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
        _apply_float('SENSOR_FAILURE_BACKOFF_SECONDS', 'sensor_failure_backoff_seconds')
        _apply_float('SENSOR_READ_HARD_TIMEOUT', 'sensor_read_hard_timeout')
        _apply_float('BMS_READ_HARD_TIMEOUT', 'bms_read_hard_timeout')
        _apply_int('OUTPUT_ACTIONS_PER_CYCLE', 'output_actions_per_cycle')
        _apply_float('OUTPUT_RETRY_DELAY', 'output_retry_delay')
        _apply_float('OUTPUT_RESPONSE_TIMEOUT', 'output_response_timeout')
        _apply_float('OUTPUT_SETTLE_DELAY', 'output_settle_delay')
        _apply_int('OUTPUT_MAX_RETRIES', 'output_max_retries')
        _apply_float('PHOTOELECTRIC_POLL_INTERVAL', 'photoelectric_poll_interval')
        _apply_float('OBSTACLE_OUTPUT_DELAY', 'obstacle_output_delay')
        _apply_float('OUTPUT_STARTUP_HOLDOFF_SECONDS', 'output_startup_holdoff_seconds')
        _apply_float('OUTPUT_IDLE_GAP_SECONDS', 'output_idle_gap_seconds')
        _apply_float('IO_DEGRADED_HOLDOFF_SECONDS', 'io_degraded_holdoff_seconds')
        _apply_float('PHOTOELECTRIC_AFTER_OUTPUT_GAP_SECONDS', 'photoelectric_after_output_gap_seconds')
        _apply_float('ENV_SENSOR_GROUP_INTERVAL', 'env_sensor_group_interval')
        _apply_float('GAS_SENSOR_GROUP_INTERVAL', 'gas_sensor_group_interval')
        _apply_float('BMS_GROUP_INTERVAL', 'bms_group_interval')
        _apply_float('BMS_FAST_GROUP_INTERVAL', 'bms_fast_group_interval')
        _apply_float('BMS_SLOW_GROUP_INTERVAL', 'bms_slow_group_interval')
        _apply_float('GATEWAY_HEARTBEAT_SECONDS', 'gateway_heartbeat_seconds')
        _apply_float('GATEWAY_WATCHDOG_TIMEOUT', 'gateway_watchdog_timeout')
        _apply_float('MQTT_DEGRADE_ENTER_SECONDS', '_degrade_enter_seconds')
        _apply_float('MQTT_DEGRADE_EXIT_SECONDS', '_degrade_exit_seconds')
        _apply_float('MQTT_DEGRADE_FACTOR', '_degrade_factor')
        _apply_float('ALARM_OUTPUT_HOLD_SECONDS', 'alarm_output_hold_seconds')
        _apply_int('ADDRESS_SUSPECT_FAILURES', 'address_suspect_failures')
        _apply_int('ADDRESS_DEGRADED_FAILURES', 'address_degraded_failures')
        _apply_int('ADDRESS_RECOVERY_SUCCESSES', 'address_recovery_successes')
        _apply_float('ADDRESS_SUSPECT_BACKOFF_SECONDS', 'address_suspect_backoff_seconds')
        _apply_float('ADDRESS_DEGRADED_BACKOFF_SECONDS', 'address_degraded_backoff_seconds')
        _apply_float('SERIAL_STALL_HOLDOFF_SECONDS', 'serial_stall_holdoff_seconds')
        _apply_float('SENSOR_STALE_MAX_AGE_SECONDS', 'sensor_stale_max_age_seconds')
        if 'ENV_SENSOR_GROUP_PHASE_OFFSET' in overrides:
            self.group_phase_offsets['env'] = float(overrides['ENV_SENSOR_GROUP_PHASE_OFFSET'])
            applied.append('ENV_SENSOR_GROUP_PHASE_OFFSET')
        if 'GAS_SENSOR_GROUP_PHASE_OFFSET' in overrides:
            self.group_phase_offsets['gas'] = float(overrides['GAS_SENSOR_GROUP_PHASE_OFFSET'])
            applied.append('GAS_SENSOR_GROUP_PHASE_OFFSET')
        if 'PHOTOELECTRIC_GROUP_PHASE_OFFSET' in overrides:
            self.group_phase_offsets['photoelectric'] = float(overrides['PHOTOELECTRIC_GROUP_PHASE_OFFSET'])
            applied.append('PHOTOELECTRIC_GROUP_PHASE_OFFSET')
        if 'BMS_FAST_GROUP_PHASE_OFFSET' in overrides:
            self.group_phase_offsets['bms_fast'] = float(overrides['BMS_FAST_GROUP_PHASE_OFFSET'])
            applied.append('BMS_FAST_GROUP_PHASE_OFFSET')
        if 'BMS_SLOW_GROUP_PHASE_OFFSET' in overrides:
            self.group_phase_offsets['bms_slow'] = float(overrides['BMS_SLOW_GROUP_PHASE_OFFSET'])
            applied.append('BMS_SLOW_GROUP_PHASE_OFFSET')

        if 'ENABLED_SENSOR_TYPES' in overrides:
            enabled = overrides.get('ENABLED_SENSOR_TYPES')
            if isinstance(enabled, str):
                enabled = [item.strip() for item in enabled.split(',')]
            if isinstance(enabled, (list, tuple, set)):
                self.enabled_sensor_types = {
                    str(item).strip().lower()
                    for item in enabled
                    if str(item).strip()
                }
                self._rebuild_enabled_sensors()
                applied.append('ENABLED_SENSOR_TYPES')

        if 'LOCAL_SENSOR_THRESHOLDS' in overrides:
            thresholds = self._coerce_thresholds(overrides.get('LOCAL_SENSOR_THRESHOLDS'))
            if thresholds is not None:
                self.local_sensor_thresholds = thresholds
                self._persist_hot_overrides({'LOCAL_SENSOR_THRESHOLDS': thresholds})
                self._config_aligned = True
                applied.append('LOCAL_SENSOR_THRESHOLDS')
                logging.info("Local sensor thresholds aligned keys=%s", sorted(thresholds.keys()))

        if 'GATEWAY_WATCHDOG_ENABLED' in overrides:
            self.gateway_watchdog_enabled = self._coerce_bool(
                overrides.get('GATEWAY_WATCHDOG_ENABLED'),
                self.gateway_watchdog_enabled,
            )
            applied.append('GATEWAY_WATCHDOG_ENABLED')
        if 'GATEWAY_EXIT_ON_BLOCKING_TIMEOUT' in overrides:
            self.gateway_exit_on_blocking_timeout = self._coerce_bool(
                overrides.get('GATEWAY_EXIT_ON_BLOCKING_TIMEOUT'),
                self.gateway_exit_on_blocking_timeout,
            )
            applied.append('GATEWAY_EXIT_ON_BLOCKING_TIMEOUT')

        mqtt_keys = [key for key in overrides.keys() if key.startswith('MQTT_')]
        if mqtt_keys:
            self.mqtt_client.apply_runtime_config(overrides)
            applied.extend([key for key in mqtt_keys if key not in applied])

        return applied

    def _rebuild_enabled_sensors(self) -> None:
        self.sensors.clear()
        self._sensor_backoff_until.clear()
        self.initialize_sensors()

    def _persist_hot_overrides(self, overrides: Dict[str, Any]) -> None:
        try:
            current = load_overrides()
            save_overrides(merge_overrides(current, overrides))
        except Exception as exc:
            logging.error("Failed to persist hot config overrides: %s", exc)

    def handle_rfid_data(self, rfid_data: dict) -> None:
        rfid_data.setdefault('schema', MESSAGE_SCHEMA_VERSION)
        with_payload_type(rfid_data, 'rfid_data')
        self.mqtt_client.publish(MQTT_TOPICS['rfid_data'], rfid_data)
        logging.info("RFID report card_id=%s", rfid_data.get('card_id'))

    def handle_alarm_broadcast(self, payload: bytes) -> None:
        try:
            text = payload.decode('utf-8', errors='ignore').strip()
            data = json.loads(text) if text else {}
        except Exception as exc:
            logging.error("Alarm broadcast parse failed: %s", exc)
            return

        if not isinstance(data, dict):
            return
        if data.get('event') != 'alarm':
            return
        if not self._is_dangerous_gas_alarm_event(data):
            logging.info(
                "Alarm broadcast ignored for non-gas source=%s sensor=%s",
                data.get('source'),
                self._extract_alarm_sensor_key(data),
            )
            return

        self._alarm_hold_until = time.monotonic() + max(0.0, self.alarm_output_hold_seconds)
        self._set_alarm_output(True, source=str(data.get('source') or 'mqtt_alarm'))

    # IO board controls -------------------------------------------------
    def _set_startup_outputs(self) -> None:
        if not STARTUP_IO_OUTPUTS_ENABLED:
            logging.info("Startup IO outputs disabled by config")
            return
        desired_states = {
            'running': True,
            'communication': True,
            'charge': True,
            'camera': False,
            'alarm': False,
        }
        for name, state in desired_states.items():
            self._queue_output_action(
                name,
                state,
                source='startup',
                force=True,
            )
        logging.info("Startup IO outputs queued count=%s", len(desired_states))

    def _check_camera_and_update_light(self) -> None:
        if self._camera_checked:
            return
        self._camera_checked = True
        if not CAMERA_LIGHT_OUTPUT_ENABLED:
            logging.info("Camera light output disabled by config")
            return
        if not CAMERA_HOST:
            logging.warning("Camera target is empty, camera light remains off")
            self._schedule_camera_light_update(False)
            return

        reachable = False
        try:
            with socket.create_connection((CAMERA_HOST, int(CAMERA_PORT)), timeout=float(CAMERA_TIMEOUT)):
                reachable = True
        except OSError as exc:
            logging.warning(
                "Camera probe failed target=%s host=%s port=%s error=%s",
                CAMERA_TARGET,
                CAMERA_HOST,
                CAMERA_PORT,
                exc,
            )

        logging.info(
            "Camera probe target=%s host=%s port=%s reachable=%s",
            CAMERA_TARGET,
            CAMERA_HOST,
            CAMERA_PORT,
            reachable,
        )
        self._queue_output_action(
            'camera',
            reachable,
            source='camera_probe',
            force=True,
        )

    def _schedule_camera_light_update(self, enabled: bool) -> None:
        self._queue_output_action(
            'camera',
            enabled,
            source='camera_probe',
            force=True,
        )

    def _update_obstacle_output(self, sensor_payload: list[dict]) -> None:
        obstacle_items = [
            item
            for item in sensor_payload
            if item.get('sensor_type') == 'photoelectric'
        ]
        if not obstacle_items:
            return

        obstacle_active = any(bool(item.get('obstacle_detected')) for item in obstacle_items)
        if obstacle_active != self._obstacle_active:
            self._queue_output_action(
                'obstacle',
                obstacle_active,
                source='photoelectric',
                due_at=time.monotonic() + self.obstacle_output_delay,
            )
            self._obstacle_active = obstacle_active

    def _update_alarm_output(self, sensor_payload: list[dict]) -> None:
        alarm_active = bool(self._find_threshold_violations(sensor_payload))
        if self._alarm_hold_until > time.monotonic():
            alarm_active = True
        self._set_alarm_output(alarm_active, source='local_threshold')

    def _set_alarm_output(self, alarm_active: bool, *, source: str) -> None:
        if alarm_active != self._alarm_active:
            self._queue_output_action('alarm', alarm_active, source=source)
            self._alarm_active = alarm_active
            logging.info("Alarm output set state=%s source=%s", alarm_active, source)

    def _queue_output_action(
        self,
        name: str,
        enabled: bool,
        *,
        source: str,
        force: bool = False,
        due_at: Optional[float] = None,
    ) -> None:
        normalized = str(name).strip().lower()
        self._pending_output_actions[normalized] = {
            'name': normalized,
            'enabled': bool(enabled),
            'force': bool(force),
            'source': source,
            'queued_at': time.monotonic(),
            'attempts': 0,
            'due_at': due_at or 0.0,
        }

    async def _process_output_actions(self) -> None:
        if self.output_actions_per_cycle <= 0:
            self._pending_output_actions.clear()
            return
        if not self._pending_output_actions:
            return
        if self._io_degraded_until > time.monotonic():
            return
        now = time.monotonic()
        if now - self._started_at < self.output_startup_holdoff_seconds:
            return
        if now - self._last_bus_activity < self.output_idle_gap_seconds:
            return

        for _ in range(min(self.output_actions_per_cycle, len(self._pending_output_actions))):
            name, action = self._pending_output_actions.popitem(last=False)
            due_at = float(action.get('due_at', 0.0) or 0.0)
            now = time.monotonic()
            if due_at > now:
                self._pending_output_actions[name] = action
                continue
            try:
                success = self.io_board.set_output(
                    name,
                    action['enabled'],
                    force=action['force'],
                    wait_for_tx_complete=True,
                    response_timeout=self.output_response_timeout,
                )
            except Exception as exc:
                success = False
                logging.error("Queued IO output failed name=%s source=%s error=%s", name, action['source'], exc)

            if success:
                self._last_bus_activity = time.monotonic()
                self._last_output_activity = self._last_bus_activity
                logging.info(
                    "Queued IO output applied name=%s state=%s source=%s",
                    name,
                    action['enabled'],
                    action['source'],
                )
            else:
                action['attempts'] += 1
                if self.output_max_retries and action['attempts'] >= self.output_max_retries:
                    self._io_degraded_until = time.monotonic() + self.io_degraded_holdoff_seconds
                    self._io_degraded_reason = name
                    logging.error(
                        "IO degraded after output retries name=%s attempts=%s holdoff=%.2fs",
                        name,
                        action['attempts'],
                        self.io_degraded_holdoff_seconds,
                    )
                elif self.running:
                    logging.warning(
                        "Queued IO output retry name=%s state=%s source=%s attempts=%s",
                        name,
                        action['enabled'],
                        action['source'],
                        action['attempts'],
                    )
                    action['due_at'] = time.monotonic() + self.output_retry_delay
                    self._pending_output_actions[name] = action

            if self.output_settle_delay > 0:
                await asyncio.sleep(self.output_settle_delay)

    def _find_threshold_violations(self, sensor_payload: list[dict]) -> list[dict]:
        violations: list[dict] = []
        for item in sensor_payload:
            sensor_type = str(item.get('sensor_type') or '').strip()
            if not sensor_type:
                continue
            if sensor_type not in DANGEROUS_GAS_SENSOR_TYPES:
                continue
            threshold = self.local_sensor_thresholds.get(sensor_type)
            if not threshold:
                continue

            value = item.get('value')
            if not isinstance(value, (int, float)):
                continue

            min_value = threshold.get('min')
            max_value = threshold.get('max')
            alarm_type: Optional[str] = None
            if min_value is not None and value < float(min_value):
                alarm_type = 'low'
            elif max_value is not None and value > float(max_value):
                alarm_type = 'high'

            if alarm_type:
                violations.append(
                    {
                        'sensor_type': sensor_type,
                        'value': value,
                        'alarm_type': alarm_type,
                        'min': min_value,
                        'max': max_value,
                    }
                )

        if violations:
            logging.warning("Local sensor threshold alarm violations=%s", violations)
        return violations

    @staticmethod
    def _extract_alarm_sensor_key(event: Dict[str, Any]) -> str:
        payload = event.get('payload')
        if isinstance(payload, dict):
            for key in ('sensor_key', 'sensor_type', 'type'):
                value = payload.get(key)
                if value:
                    return str(value).strip()
        for key in ('sensor_key', 'sensor_type', 'type'):
            value = event.get(key)
            if value:
                return str(value).strip()
        return ''

    @classmethod
    def _is_dangerous_gas_alarm_event(cls, event: Dict[str, Any]) -> bool:
        return cls._extract_alarm_sensor_key(event) in DANGEROUS_GAS_SENSOR_TYPES

    @staticmethod
    def _coerce_thresholds(value: Any) -> Optional[Dict[str, Dict[str, Optional[float]]]]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        if not isinstance(value, dict):
            return None

        thresholds: Dict[str, Dict[str, Optional[float]]] = {}
        for sensor_type, bounds in value.items():
            if not isinstance(bounds, dict):
                continue
            thresholds[str(sensor_type)] = {
                'min': SensorGateway._coerce_optional_float(bounds.get('min')),
                'max': SensorGateway._coerce_optional_float(bounds.get('max')),
            }
        return thresholds

    @staticmethod
    def _coerce_optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        normalized = str(value).strip().lower()
        if normalized in ('1', 'true', 'yes', 'on'):
            return True
        if normalized in ('0', 'false', 'no', 'off'):
            return False
        return default

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

    def _record_transaction_metric(self, **payload) -> None:
        operation = str(payload.get('operation') or 'unknown')
        address = payload.get('address')
        status = str(payload.get('status') or 'unknown')
        key = f"{operation}:{address}"
        counts = self._transaction_metrics['counts'].setdefault(key, {})
        counts[status] = counts.get(status, 0) + 1
        durations = self._transaction_metrics['durations_ms'].setdefault(key, [])
        duration_ms = float(payload.get('duration_ms') or 0.0)
        durations.append(duration_ms)
        if len(durations) > 20:
            del durations[:-20]
        self._transaction_metrics['last'][key] = dict(payload)

    def _ensure_address_health(self, address: int) -> Dict[str, Any]:
        state = self._address_health.get(address)
        if state is None:
            state = {
                'status': 'healthy',
                'consecutive_failures': 0,
                'consecutive_successes': 0,
                'backoff_until': 0.0,
                'last_error': None,
            }
            self._address_health[address] = state
        return state

    def _mark_address_result(self, address: int, *, success: bool, error: Optional[str] = None) -> None:
        state = self._ensure_address_health(int(address))
        now = time.monotonic()
        if success:
            state['consecutive_failures'] = 0
            state['consecutive_successes'] += 1
            if state['status'] != 'healthy' and state['consecutive_successes'] >= self.address_recovery_successes:
                state['status'] = 'healthy'
                state['backoff_until'] = 0.0
            return

        state['consecutive_successes'] = 0
        state['consecutive_failures'] += 1
        state['last_error'] = error or 'read_failed'
        if error == 'stall':
            state['status'] = 'degraded'
            state['backoff_until'] = now + self.serial_stall_holdoff_seconds
            return
        if state['consecutive_failures'] >= self.address_degraded_failures:
            state['status'] = 'degraded'
            state['backoff_until'] = now + self.address_degraded_backoff_seconds
        elif state['consecutive_failures'] >= self.address_suspect_failures:
            state['status'] = 'suspect'
            state['backoff_until'] = now + self.address_suspect_backoff_seconds

    def _address_available(self, address: int) -> bool:
        state = self._ensure_address_health(int(address))
        return float(state.get('backoff_until', 0.0) or 0.0) <= time.monotonic()

    # Sensor loop --------------------------------------------------------
    async def read_sensors_loop(self) -> None:
        while self.running:
            try:
                self._mark_loop_progress()
                self._log_loop_heartbeat()
                self._update_degraded_state(time.monotonic())
                self.mqtt_client.ensure_connected()
                sensor_payload = []
                bms_payload_to_publish: Optional[dict] = None
                failed_sensors = []
                due_groups = self._get_due_groups(time.monotonic())
                if due_groups:
                    group = due_groups[0]
                    if group.startswith('bms_'):
                        bms_data = await self._read_bms_group(group)
                        if bms_data:
                            bms_payload_to_publish = self._merge_bms_snapshot(bms_data)
                        self._last_group_poll[group] = time.monotonic()
                        self._mark_loop_progress()
                    else:
                        group_sensors = self._get_group_sensors(group)
                        if not group_sensors:
                            self._last_group_poll[group] = time.monotonic()
                        else:
                            group_failed, group_payload = await self._poll_sensor_group(group_sensors)
                            failed_sensors.extend(group_failed)
                            merged_payload = self._merge_sensor_snapshot(group, group_payload)
                            sensor_payload.extend(merged_payload)
                            self._last_group_poll[group] = time.monotonic()
                        self._mark_loop_progress()

                if sensor_payload:
                    self._update_obstacle_output(sensor_payload)
                    self._update_alarm_output(sensor_payload)
                    self.mqtt_client.publish(MQTT_TOPICS['sensor_data'], sensor_payload)
                    self._last_bus_activity = time.monotonic()
                if bms_payload_to_publish:
                    bms_payload_to_publish.setdefault('schema', MESSAGE_SCHEMA_VERSION)
                    with_payload_type(bms_payload_to_publish, 'bms_data')
                    self.mqtt_client.publish(MQTT_TOPICS['bms_data'], bms_payload_to_publish)
                    self._last_bus_activity = time.monotonic()
                await self._process_output_actions()
                self._mark_loop_progress()
                if failed_sensors:
                    logging.warning(
                        "Sensors skipped in batch: %s",
                        ", ".join(failed_sensors),
                    )
            except Exception as exc:
                logging.error("Failed to read sensors: %s", exc)

            factor = self._degrade_factor if self._degraded else 1.0
            await asyncio.sleep(self.loop_idle_delay * factor)

    def _get_due_groups(self, now: float) -> list[str]:
        schedule: list[tuple[str, float]] = [
            ('photoelectric', self.photoelectric_poll_interval),
            ('env', self.env_sensor_group_interval),
            ('gas', self.gas_sensor_group_interval),
        ]
        if self.bms_sensor:
            schedule.append(('bms_fast', self.bms_fast_group_interval))
            schedule.append(('bms_slow', self.bms_slow_group_interval))

        due: list[str] = []
        priority_first = ['photoelectric', 'env', 'gas', 'bms_fast', 'bms_slow']
        schedule_map = {group: interval for group, interval in schedule}
        ordered_groups = [group for group in priority_first if group in schedule_map]

        for group in ordered_groups:
            interval = schedule_map[group]
            last_poll = self._last_group_poll.get(group, 0.0)
            phase = self.group_phase_offsets.get(group, 0.0)
            effective_last = max(last_poll, self._started_at + phase - interval)
            if now - effective_last < interval:
                continue
            if group == 'photoelectric':
                if self._io_degraded_until > now:
                    continue
                if now - self._last_output_activity < self.photoelectric_after_output_gap_seconds:
                    continue
            due.append(group)

        if 'photoelectric' not in due:
            photo_last = max(self._last_group_poll.get('photoelectric', 0.0), self._last_photoelectric_read)
            if (
                'photoelectric' in schedule_map
                and self._io_degraded_until <= now
                and now - self._last_output_activity >= self.photoelectric_after_output_gap_seconds
                and now - photo_last >= min(self.photoelectric_poll_interval, 5.0)
            ):
                due.insert(0, 'photoelectric')

        return due

    def _get_group_sensors(self, group: str) -> list[Any]:
        if group == 'env':
            wanted = {'temperature', 'humidity', 'smoke'}
        elif group == 'gas':
            wanted = {'co', 'h2s', 'o2', 'ch4'}
        elif group == 'photoelectric':
            wanted = {'photoelectric'}
        else:
            return []
        sensors = [sensor for name, sensor in self.sensors.items() if name in wanted]
        return sensors

    async def _read_bms_group(self, group: str) -> Optional[dict]:
        if not self.bms_sensor:
            return None
        label = 'bms read_fast_data' if group == 'bms_fast' else 'bms read_slow_data'
        reader = self.bms_sensor.read_fast_data if group == 'bms_fast' else self.bms_sensor.read_slow_data
        group_timeout = min(
            self.bms_read_hard_timeout,
            4.0 if group == 'bms_fast' else 6.0,
        )
        data = await self._call_blocking_with_timeout(
            reader,
            timeout=group_timeout,
            label=label,
        )
        if data:
            self._mark_address_result(self.bms_sensor.address, success=True)
            self._last_bms_read = time.monotonic()
            return data
        self._mark_address_result(self.bms_sensor.address, success=False, error=group)
        return None

    async def _poll_sensor_group(self, sensors: list[Any]) -> tuple[list[str], list[dict]]:
        failed_sensors: list[str] = []
        sensor_payload: list[dict] = []
        for sensor in sensors:
            sensor_name = getattr(sensor, 'name', sensor.__class__.__name__)
            backoff_until = self._sensor_backoff_until.get(sensor_name, 0.0)
            now = time.monotonic()
            if backoff_until > now:
                logging.info(
                    "Sensor backoff skip type=%s remaining=%.2fs",
                    sensor_name,
                    backoff_until - now,
                )
                failed_sensors.append(sensor_name)
                continue
            if not self._address_available(getattr(sensor, 'address', 0)):
                failed_sensors.append(sensor_name)
                continue

            formatted = await self._read_sensor_with_retries(sensor)
            self._last_bus_activity = time.monotonic()
            if formatted is None:
                failed_sensors.append(sensor_name)
                self._mark_address_result(sensor.address, success=False, error=getattr(sensor, '_last_read_error', sensor_name))
                if self.sensor_failure_backoff_seconds > 0:
                    self._sensor_backoff_until[sensor_name] = (
                        time.monotonic() + self.sensor_failure_backoff_seconds
                    )
                stale_payload = self._build_stale_sensor_payload(sensor)
                if stale_payload is not None:
                    sensor_payload.append(stale_payload)
                continue

            self._sensor_backoff_until.pop(sensor_name, None)
            self._mark_address_result(sensor.address, success=True)
            if sensor_name == 'photoelectric':
                self._last_photoelectric_read = time.monotonic()

            formatted.setdefault('schema', MESSAGE_SCHEMA_VERSION)
            with_payload_type(formatted, 'sensor_data')
            sensor_payload.append(formatted)

            factor = self._degrade_factor if self._degraded else 1.0
            await asyncio.sleep(self.sensor_poll_delay * factor)
        return failed_sensors, sensor_payload

    def _mark_loop_progress(self) -> None:
        self._last_loop_progress = time.monotonic()

    def _merge_sensor_snapshot(self, group: str, payloads: list[dict]) -> list[dict]:
        if group not in {'env', 'gas'}:
            return payloads

        expected = {
            'env': {'temperature', 'humidity', 'smoke'},
            'gas': {'co', 'h2s', 'o2', 'ch4'},
        }[group]
        cache = self._snapshot_cache[group]
        now_ts = time.time()

        for payload in payloads:
            sensor_type = str(payload.get('sensor_type') or '').strip()
            if sensor_type:
                cache[sensor_type] = dict(payload)

        if expected.issubset(cache.keys()):
            merged = [dict(cache[name]) for name in sorted(expected)]
            self._snapshot_last_publish_ts[group] = now_ts
            return merged

        if self._snapshot_last_publish_ts[group] <= 0.0:
            return []
        if now_ts - self._snapshot_last_publish_ts[group] >= 60.0:
            degraded = [dict(cache[name]) for name in sorted(cache.keys()) if name in expected]
            for payload in degraded:
                payload['stale'] = True
                age_ts = payload.get('ts')
                if isinstance(age_ts, (int, float)):
                    payload['stale_age_seconds'] = round(now_ts - float(age_ts), 3)
                payload.setdefault('schema', MESSAGE_SCHEMA_VERSION)
                with_payload_type(payload, 'sensor_data')
            return degraded
        return []

    def _merge_bms_snapshot(self, payload: dict) -> Optional[dict]:
        cache = self._snapshot_cache['bms']
        data = payload.get('data') or {}
        for key, value in data.items():
            if value is not None:
                cache[key] = value

        expected = {'voltage', 'soc', 'status', 'capacity', 'power', 'cell_voltages', 'current'}
        merged_data = {key: cache.get(key) for key in expected if key in cache}
        if expected.issubset(merged_data.keys()):
            self._snapshot_last_publish_ts['bms'] = time.time()
            merged = dict(payload)
            merged['data'] = merged_data
            return merged

        if self._snapshot_last_publish_ts['bms'] <= 0.0:
            return None
        if time.time() - self._snapshot_last_publish_ts['bms'] >= 60.0:
            degraded = dict(payload)
            degraded['data'] = merged_data
            return degraded
        return None

    def _log_loop_heartbeat(self) -> None:
        now = time.monotonic()
        if now - self._last_loop_heartbeat < self.gateway_heartbeat_seconds:
            return
        self._last_loop_heartbeat = now
        logging.info(
            "Gateway heartbeat running=%s mqtt_connected=%s sensors=%s bms=%s io_degraded=%s txn_keys=%s",
            self.running,
            bool(self.mqtt_client.connected),
            len(self.sensors),
            bool(self.bms_sensor),
            self._io_degraded_until > now,
            len(self._transaction_metrics['counts']),
        )

    def _start_watchdog(self) -> None:
        if not self.gateway_watchdog_enabled:
            logging.info("Gateway watchdog disabled")
            return
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name='gateway-watchdog',
            daemon=True,
        )
        self._watchdog_thread.start()
        logging.info(
            "Gateway watchdog started timeout=%.2fs",
            self.gateway_watchdog_timeout,
        )

    def _watchdog_loop(self) -> None:
        while self.running:
            time.sleep(min(max(self.gateway_watchdog_timeout / 4.0, 5.0), 30.0))
            if not self.running or not self.gateway_watchdog_enabled:
                continue
            stalled_for = time.monotonic() - self._last_loop_progress
            if stalled_for < self.gateway_watchdog_timeout:
                continue
            logging.critical(
                "Gateway watchdog timeout stalled_for=%.2fs timeout=%.2fs exiting for supervisor restart",
                stalled_for,
                self.gateway_watchdog_timeout,
            )
            os._exit(2)

    async def _call_blocking_with_timeout(self, func, *, timeout: float, label: str):
        try:
            return await asyncio.wait_for(asyncio.to_thread(func), timeout=timeout)
        except asyncio.TimeoutError:
            logging.error("Blocking call timeout label=%s timeout=%.2fs", label, timeout)
            if self.gateway_exit_on_blocking_timeout:
                logging.critical(
                    "Exiting after blocking call timeout label=%s for supervisor restart",
                    label,
                )
                os._exit(3)
            return None

    async def _read_sensor_with_retries(self, sensor: Any) -> Optional[dict]:
        sensor_name = getattr(sensor, 'name', sensor.__class__.__name__)
        sensor_address = getattr(sensor, 'address', 'unknown')
        sensor_attempts = int(getattr(sensor, 'max_attempts', 0) or 0)
        max_attempts = max(1, sensor_attempts or self.max_sensor_attempts)
        sensor_timeout = self._sensor_timeout_budget(sensor)
        retry_delay = self._sensor_retry_delay(sensor)
        sensor._last_read_error = None
        sensor._last_read_attempt_count = 0
        sensor._last_first_attempt_success = False
        sensor._last_read_recovered = False

        for attempt in range(1, max_attempts + 1):
            sensor._last_read_attempt_count = attempt
            try:
                formatted = await self._call_blocking_with_timeout(
                    sensor.get_formatted_data,
                    timeout=sensor_timeout,
                    label=f'sensor {sensor_name} address={sensor_address}',
                )
            except Exception as sensor_exc:
                logging.error(
                    "Sensor read exception type=%s address=%s attempt=%s/%s error=%s",
                    sensor_name,
                    sensor_address,
                    attempt,
                    max_attempts,
                    sensor_exc,
                )
                formatted = None
                sensor._last_read_error = 'exception'

            if formatted:
                sensor._last_first_attempt_success = attempt == 1
                sensor._last_read_recovered = attempt > 1
                if attempt > 1:
                    logging.info(
                        "Sensor read recovered type=%s address=%s attempt=%s/%s",
                        sensor_name,
                        sensor_address,
                        attempt,
                        max_attempts,
                    )
                return formatted

            if sensor._last_read_error is None:
                sensor._last_read_error = 'read_failed'
            logging.warning(
                "Sensor read missing type=%s address=%s attempt=%s/%s",
                sensor_name,
                sensor_address,
                attempt,
                max_attempts,
            )

            if attempt < max_attempts:
                await asyncio.sleep(retry_delay)

        return None

    def _sensor_retry_delay(self, sensor: Any) -> float:
        retry_delay = getattr(sensor, 'retry_delay', None)
        if isinstance(retry_delay, (int, float)) and retry_delay >= 0:
            return float(retry_delay)
        return max(0.0, float(self.sensor_retry_delay))

    def _sensor_timeout_budget(self, sensor: Any) -> float:
        direct_timeout = getattr(sensor, 'direct_timeout', None)
        response_timeout = getattr(sensor, 'response_timeout', None)
        direct_retries = max(1, int(getattr(sensor, 'direct_retries', 1) or 1))
        sensor_attempts = max(1, int(getattr(sensor, 'max_attempts', 1) or 1))
        retry_delay = self._sensor_retry_delay(sensor)
        if isinstance(direct_timeout, (int, float)) and direct_timeout > 0:
            estimated = (float(direct_timeout) * direct_retries) + (retry_delay * max(sensor_attempts - 1, 0)) + 0.8
            return min(self.sensor_read_hard_timeout, max(estimated, float(direct_timeout) + 0.8))
        if isinstance(response_timeout, (int, float)) and response_timeout > 0:
            estimated = (float(response_timeout) * sensor_attempts) + (retry_delay * max(sensor_attempts - 1, 0)) + 0.8
            return min(self.sensor_read_hard_timeout, max(estimated, float(response_timeout) + 0.8))
        return self.sensor_read_hard_timeout

    def _build_stale_sensor_payload(self, sensor: Any) -> Optional[dict]:
        last_value = getattr(sensor, 'last_value', None)
        last_payload = getattr(sensor, '_last_payload', None)
        if isinstance(last_payload, dict):
            age_ts = last_payload.get('ts')
            if isinstance(age_ts, (int, float)):
                age = time.time() - float(age_ts)
                if age <= self.sensor_stale_max_age_seconds:
                    payload = dict(last_payload)
                    now = datetime.now().astimezone()
                    payload['timestamp'] = now.isoformat()
                    payload['ts'] = now.timestamp()
                    payload['stale'] = True
                    payload['stale_age_seconds'] = round(age, 3)
                    payload.setdefault('schema', MESSAGE_SCHEMA_VERSION)
                    with_payload_type(payload, 'sensor_data')
                    return payload
        if last_value is None:
            return None
        last_ts = getattr(sensor, '_last_success_ts', None)
        if not isinstance(last_ts, (int, float)):
            return None
        age = time.time() - float(last_ts)
        if age > self.sensor_stale_max_age_seconds:
            return None

        now = datetime.now().astimezone()
        payload = {
            'sensor_type': getattr(sensor, 'name', sensor.__class__.__name__),
            'address': getattr(sensor, 'address', 0),
            'value': last_value,
            'timestamp': now.isoformat(),
            'ts': now.timestamp(),
            'stale': True,
            'stale_age_seconds': round(age, 3),
        }
        payload.setdefault('schema', MESSAGE_SCHEMA_VERSION)
        with_payload_type(payload, 'sensor_data')
        return payload

    async def _run_tasks(self) -> None:
        tasks = [
            asyncio.create_task(self.read_sensors_loop()),
            asyncio.create_task(self.config_alignment_loop()),
        ]
        await asyncio.gather(*tasks)

    async def config_alignment_loop(self) -> None:
        intervals = [5.0, 10.0, 30.0]
        while self.running and not self._config_aligned:
            await asyncio.sleep(intervals[min(self._hello_attempts, len(intervals) - 1)])
            if not self.running or self._config_aligned:
                break
            self.send_device_hello(reason='retry')

        while self.running:
            await asyncio.sleep(300.0)
            if self.running:
                self.send_device_hello(reason='periodic')

    # Lifecycle ----------------------------------------------------------
    def start(self) -> None:
        try:
            logging.info("Starting sensor gateway...")
            self._preflight_release_serial_port()
            self.running = True

            self.mqtt_client.connect()
            time.sleep(2)

            self.setup_mqtt_callbacks()
            self.send_device_hello()
            self.initialize_sensors()
            self._start_watchdog()
            self._set_startup_outputs()
            self._check_camera_and_update_light()
            self.rfid_reader.start_reading(self.handle_rfid_data)

            asyncio.run(self._run_tasks())
        except KeyboardInterrupt:
            logging.info("Stop signal received")
        except Exception as exc:
            logging.error("Gateway runtime error: %s", exc)
        finally:
            self.stop()

    def stop(self) -> None:
        if not self.running:
            self.serial_manager.close_all()
            self.mqtt_client.disconnect()
            return

        logging.info("Stopping sensor gateway...")
        self.running = False
        self.rfid_reader.stop_reading()
        startup_thread = self._startup_outputs_thread
        if startup_thread and startup_thread.is_alive():
            startup_thread.join(timeout=1.0)
        self.serial_manager.close_all()
        self.mqtt_client.disconnect()

    def send_device_hello(self, reason: str = 'startup') -> None:
        self._hello_attempts += 1
        payload: Dict[str, Any] = {
            'config_version': get_config_version(),
            'last_ts': get_config_updated_at(),
            'device_id': MQTT_CONFIG.get('client_id'),
            'reason': reason,
            'attempt': self._hello_attempts,
            'timestamp': self._get_timestamp(),
            'ts': self._get_ts(),
            'schema': MESSAGE_SCHEMA_VERSION,
        }
        with_payload_type(payload, 'device_hello')
        self.mqtt_client.publish(MQTT_TOPICS['device_hello'], payload)


if __name__ == "__main__":
    SensorGateway().start()
