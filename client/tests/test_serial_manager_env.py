from __future__ import annotations

import importlib
import sys
import types


def _install_dummy_serial_modules() -> None:
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


def test_serial_direct_overrides_from_env(monkeypatch) -> None:
    _install_dummy_serial_modules()
    monkeypatch.setenv("SERIAL_DIRECT_RETRIES", "5")
    monkeypatch.setenv("SERIAL_DIRECT_RESPONSE_DELAY", "0.12")
    monkeypatch.setenv("SERIAL_DIRECT_TIMEOUT", "2.5")

    config = importlib.import_module("client.config")
    importlib.reload(config)

    from client.communication.serial_manager import SerialManager

    manager = SerialManager(**config.SERIAL_CONFIG)

    assert manager.direct_retries == 5
    assert manager.direct_response_delay == 0.12
    assert manager.direct_timeout == 2.5
