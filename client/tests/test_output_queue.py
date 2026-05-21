from __future__ import annotations

import asyncio
import sys
import time
import types


def _install_dummy_modules() -> None:
    minimalmodbus_mod = types.ModuleType("minimalmodbus")

    class DummyInstrument:
        def __init__(self, *args, **kwargs):
            self.serial = types.SimpleNamespace(is_open=False)

    minimalmodbus_mod.Instrument = DummyInstrument
    sys.modules["minimalmodbus"] = minimalmodbus_mod

    serial_mod = types.ModuleType("serial")
    serial_mod.PARITY_NONE = "N"

    class DummySerial:
        pass

    serial_mod.Serial = DummySerial
    sys.modules["serial"] = serial_mod

    mqtt_mod = types.ModuleType("paho.mqtt.client")
    mqtt_mod.MQTT_ERR_SUCCESS = 0

    class DummyMQTTClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, *args, **kwargs):
            pass

    mqtt_mod.Client = DummyMQTTClient
    sys.modules["paho"] = types.ModuleType("paho")
    sys.modules["paho.mqtt"] = types.ModuleType("paho.mqtt")
    sys.modules["paho.mqtt.client"] = mqtt_mod


_install_dummy_modules()

import main as main_module  # noqa: E402
from main import SensorGateway  # noqa: E402
from communication.rfid_reader import RFIDReader  # noqa: E402


class FakeIOBoard:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [True])
        self.enabled = True

    def set_output(
        self,
        name,
        enabled,
        *,
        force=False,
        wait_for_tx_complete=True,
        response_timeout=None,
    ):
        self.calls.append((name, enabled, force, wait_for_tx_complete, response_timeout))
        if self.responses:
            return self.responses.pop(0)
        return True


def test_startup_outputs_are_queued_not_sent_immediately() -> None:
    gateway = SensorGateway()
    fake_io = FakeIOBoard()
    gateway.io_board = fake_io

    main_module.STARTUP_IO_OUTPUTS_ENABLED = True
    gateway._set_startup_outputs()

    assert fake_io.calls == []
    assert set(gateway._pending_output_actions.keys()) == {
        "running",
        "communication",
        "charge",
        "camera",
        "alarm",
    }


def test_startup_outputs_can_be_disabled() -> None:
    gateway = SensorGateway()

    main_module.STARTUP_IO_OUTPUTS_ENABLED = False
    gateway._set_startup_outputs()

    assert gateway._pending_output_actions == {}


def test_process_output_actions_sends_one_item_per_cycle() -> None:
    gateway = SensorGateway()
    fake_io = FakeIOBoard([True, True])
    gateway.io_board = fake_io
    gateway.output_actions_per_cycle = 1
    gateway.output_response_timeout = 0.4
    gateway.output_startup_holdoff_seconds = 0.0
    gateway.output_idle_gap_seconds = 0.0
    gateway._started_at = time.monotonic() - 10.0

    gateway._queue_output_action("running", True, source="test", force=True)
    gateway._queue_output_action("camera", False, source="test", force=True)

    asyncio.run(gateway._process_output_actions())
    assert len(fake_io.calls) == 1
    assert fake_io.calls[0][:2] == ("running", True)
    assert list(gateway._pending_output_actions.keys()) == ["camera"]

    asyncio.run(gateway._process_output_actions())
    assert len(fake_io.calls) == 2
    assert fake_io.calls[1][:2] == ("camera", False)
    assert gateway._pending_output_actions == {}


def test_failed_output_action_is_requeued_with_delay() -> None:
    gateway = SensorGateway()
    fake_io = FakeIOBoard([False])
    gateway.io_board = fake_io
    gateway.running = True
    gateway.output_actions_per_cycle = 1
    gateway.output_retry_delay = 1.5
    gateway.output_startup_holdoff_seconds = 0.0
    gateway.output_idle_gap_seconds = 0.0
    gateway._started_at = time.monotonic() - 10.0

    gateway._queue_output_action("alarm", True, source="test", force=False)
    asyncio.run(gateway._process_output_actions())

    assert len(fake_io.calls) == 1
    pending = gateway._pending_output_actions["alarm"]
    assert pending["attempts"] == 1
    assert pending["due_at"] > pending["queued_at"]


def test_obstacle_output_is_deferred_after_photoelectric_change() -> None:
    gateway = SensorGateway()
    gateway.obstacle_output_delay = 2.0
    gateway._obstacle_active = False

    gateway._update_obstacle_output(
        [{"sensor_type": "photoelectric", "obstacle_detected": True}]
    )

    pending = gateway._pending_output_actions["obstacle"]
    assert pending["enabled"] is True
    assert pending["due_at"] >= pending["queued_at"] + 1.9


def test_output_actions_respect_startup_holdoff() -> None:
    gateway = SensorGateway()
    fake_io = FakeIOBoard([True])
    gateway.io_board = fake_io
    gateway.output_startup_holdoff_seconds = 10.0
    gateway._started_at = time.monotonic()
    gateway._queue_output_action("running", True, source="test", force=True)

    asyncio.run(gateway._process_output_actions())

    assert fake_io.calls == []
    assert "running" in gateway._pending_output_actions


def test_output_actions_wait_for_idle_gap() -> None:
    gateway = SensorGateway()
    fake_io = FakeIOBoard([True])
    gateway.io_board = fake_io
    gateway.output_startup_holdoff_seconds = 0.0
    gateway.output_idle_gap_seconds = 1.0
    gateway._started_at = time.monotonic() - 10.0
    gateway._last_bus_activity = time.monotonic()
    gateway._queue_output_action("running", True, source="test", force=True)

    asyncio.run(gateway._process_output_actions())

    assert fake_io.calls == []
    assert "running" in gateway._pending_output_actions


def test_group_scheduler_respects_due_intervals() -> None:
    gateway = SensorGateway()
    gateway.bms_sensor = object()
    gateway.env_sensor_group_interval = 4.0
    gateway.gas_sensor_group_interval = 3.0
    gateway.photoelectric_poll_interval = 2.0
    gateway.bms_fast_group_interval = 6.0
    gateway.bms_slow_group_interval = 12.0
    gateway.group_phase_offsets = {
        "env": 0.0,
        "gas": 0.0,
        "photoelectric": 0.0,
        "bms_fast": 0.0,
        "bms_slow": 0.0,
    }
    gateway._started_at = 0.0
    gateway._last_group_poll = {
        "env": 0.0,
        "gas": 0.0,
        "photoelectric": 0.0,
        "bms_fast": 0.0,
        "bms_slow": 0.0,
    }

    due = gateway._get_due_groups(10.0)

    assert due == ["photoelectric", "env", "gas", "bms_fast"]


def test_group_sensor_selection_returns_full_group() -> None:
    gateway = SensorGateway()

    class DummySensor:
        def __init__(self, name):
            self.name = name

    gateway.sensors = {
        "temperature": DummySensor("temperature"),
        "humidity": DummySensor("humidity"),
        "smoke": DummySensor("smoke"),
    }

    selected = gateway._get_group_sensors("env")

    assert [item.name for item in selected] == ["temperature", "humidity", "smoke"]


def test_photoelectric_waits_after_output_activity() -> None:
    gateway = SensorGateway()
    gateway.group_phase_offsets = {
        "env": 0.0,
        "gas": 0.0,
        "photoelectric": 0.0,
        "bms_fast": 0.0,
        "bms_slow": 0.0,
    }
    gateway._started_at = 0.0
    gateway._last_group_poll = {
        "env": 100.0,
        "gas": 100.0,
        "photoelectric": 0.0,
        "bms_fast": 100.0,
        "bms_slow": 100.0,
    }
    gateway._last_output_activity = 9.6
    gateway.photoelectric_after_output_gap_seconds = 1.0

    due = gateway._get_due_groups(10.0)

    assert due == []


def test_photoelectric_has_highest_priority_when_due() -> None:
    gateway = SensorGateway()
    gateway.bms_sensor = object()
    gateway.group_phase_offsets = {
        "env": 0.0,
        "gas": 0.0,
        "photoelectric": 0.0,
        "bms_fast": 0.0,
        "bms_slow": 0.0,
    }
    gateway._started_at = 0.0
    gateway._last_group_poll = {
        "env": 0.0,
        "gas": 0.0,
        "photoelectric": 0.0,
        "bms_fast": 0.0,
        "bms_slow": 0.0,
    }

    due = gateway._get_due_groups(20.0)

    assert due[0] == "photoelectric"


def test_merge_sensor_snapshot_waits_for_complete_group() -> None:
    gateway = SensorGateway()
    gateway._snapshot_last_publish_ts["gas"] = time.time()

    partial = gateway._merge_sensor_snapshot(
        "gas",
        [
            {"sensor_type": "co", "value": 0, "ts": 1},
            {"sensor_type": "h2s", "value": 0, "ts": 1},
        ],
    )
    assert partial == []

    complete = gateway._merge_sensor_snapshot(
        "gas",
        [
            {"sensor_type": "o2", "value": 20.9, "ts": 2},
            {"sensor_type": "ch4", "value": 0, "ts": 2},
        ],
    )
    assert [item["sensor_type"] for item in complete] == ["ch4", "co", "h2s", "o2"]


def test_merge_sensor_snapshot_emits_degraded_after_timeout() -> None:
    gateway = SensorGateway()
    gateway._snapshot_last_publish_ts["env"] = time.time() - 61.0

    degraded = gateway._merge_sensor_snapshot(
        "env",
        [
            {"sensor_type": "temperature", "value": 28.0, "ts": time.time() - 10.0},
            {"sensor_type": "humidity", "value": 65.0, "ts": time.time() - 10.0},
        ],
    )

    assert [item["sensor_type"] for item in degraded] == ["humidity", "temperature"]
    assert all(item["stale"] is True for item in degraded)


def test_merge_sensor_snapshot_stays_quiet_before_first_complete_publish() -> None:
    gateway = SensorGateway()

    partial = gateway._merge_sensor_snapshot(
        "env",
        [
            {"sensor_type": "temperature", "value": 28.0, "ts": time.time()},
            {"sensor_type": "humidity", "value": 65.0, "ts": time.time()},
        ],
    )

    assert partial == []


def test_merge_bms_snapshot_prefers_complete_payload() -> None:
    gateway = SensorGateway()
    gateway._snapshot_last_publish_ts["bms"] = time.time()

    first = gateway._merge_bms_snapshot(
        {
            "sensor_type": "bms",
            "address": 210,
            "timestamp": "a",
            "ts": 1,
            "data": {"voltage": 26.2, "soc": 0.9},
        }
    )
    assert first is None

    second = gateway._merge_bms_snapshot(
        {
            "sensor_type": "bms",
            "address": 210,
            "timestamp": "b",
            "ts": 2,
            "data": {
                "status": 2.0,
                "capacity": 5.1,
                "power": 28.0,
                "cell_voltages": [3.2] * 8,
                "current": -1.1,
            },
        }
    )
    assert second is not None
    assert set(second["data"].keys()) == {
        "voltage",
        "soc",
        "status",
        "capacity",
        "power",
        "cell_voltages",
        "current",
    }


def test_sensor_timeout_budget_scales_with_retries() -> None:
    gateway = SensorGateway()
    gateway.sensor_read_hard_timeout = 10.0
    gateway.sensor_retry_delay = 0.3

    class DummySensor:
        direct_timeout = 1.2
        direct_retries = 2
        max_attempts = 2

    budget = gateway._sensor_timeout_budget(DummySensor())

    assert budget >= 3.0


def test_failed_output_action_can_degrade_io_board() -> None:
    gateway = SensorGateway()
    fake_io = FakeIOBoard([False, False, False])
    gateway.io_board = fake_io
    gateway.running = True
    gateway.output_actions_per_cycle = 1
    gateway.output_retry_delay = 0.0
    gateway.output_max_retries = 1
    gateway.io_degraded_holdoff_seconds = 30.0
    gateway.output_startup_holdoff_seconds = 0.0
    gateway.output_idle_gap_seconds = 0.0
    gateway._started_at = time.monotonic() - 10.0

    gateway._queue_output_action("alarm", True, source="test", force=False)
    asyncio.run(gateway._process_output_actions())

    assert gateway._io_degraded_until > time.monotonic()
    assert "alarm" not in gateway._pending_output_actions


def test_address_health_transitions_to_degraded_and_recovers() -> None:
    gateway = SensorGateway()
    gateway.address_suspect_failures = 2
    gateway.address_degraded_failures = 3
    gateway.address_recovery_successes = 2
    gateway.address_suspect_backoff_seconds = 10.0
    gateway.address_degraded_backoff_seconds = 20.0

    gateway._mark_address_result(15, success=False, error="timeout")
    gateway._mark_address_result(15, success=False, error="timeout")
    gateway._mark_address_result(15, success=False, error="timeout")
    state = gateway._address_health[15]
    assert state["status"] == "degraded"
    assert state["backoff_until"] > time.monotonic()

    gateway._mark_address_result(15, success=True)
    gateway._mark_address_result(15, success=True)
    state = gateway._address_health[15]
    assert state["status"] == "healthy"


def test_stop_always_releases_serial_resources() -> None:
    gateway = SensorGateway()

    class DummyRFID:
        def __init__(self):
            self.stopped = False

        def stop_reading(self):
            self.stopped = True

    class DummySerialManager:
        def __init__(self):
            self.closed = 0

        def close_all(self):
            self.closed += 1

    class DummyMQTT:
        def __init__(self):
            self.disconnected = 0

        def disconnect(self):
            self.disconnected += 1

    gateway.rfid_reader = DummyRFID()
    gateway.serial_manager = DummySerialManager()
    gateway.mqtt_client = DummyMQTT()
    gateway.running = False

    gateway.stop()

    assert gateway.serial_manager.closed == 1
    assert gateway.mqtt_client.disconnected == 1


def test_mark_loop_progress_updates_after_output_processing() -> None:
    gateway = SensorGateway()
    fake_io = FakeIOBoard([True])
    gateway.io_board = fake_io
    gateway.output_actions_per_cycle = 1
    gateway.output_startup_holdoff_seconds = 0.0
    gateway.output_idle_gap_seconds = 0.0
    gateway._started_at = time.monotonic() - 10.0
    before = gateway._last_loop_progress
    gateway._queue_output_action("running", True, source="test", force=True)

    asyncio.run(gateway._process_output_actions())
    gateway._mark_loop_progress()

    assert gateway._last_loop_progress >= before


def test_rfid_duplicate_suppression_is_time_bounded() -> None:
    reader = RFIDReader(port="/dev/ttyS5", duplicate_suppress_seconds=1.0)
    reader.last_card_id = "aa bb"
    reader.last_card_seen_ts = time.time() - 2.0

    now_ts = reader._get_ts()
    should_emit = not (
        "aa bb" == reader.last_card_id
        and reader.duplicate_suppress_seconds > 0
        and (now_ts - reader.last_card_seen_ts) < reader.duplicate_suppress_seconds
    )

    assert should_emit is True
