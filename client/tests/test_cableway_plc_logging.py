from __future__ import annotations

import logging
import sys
import types
from pathlib import Path


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


_install_dummy_serial_modules()

client_root = Path(__file__).resolve().parents[2] / "client"
sys.path.insert(0, str(client_root))

from client.communication.cableway_plc import CablewayPLC, CablewayPLCConfig


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_cableway_plc_logs_byte_order_settings() -> None:
    logger = logging.getLogger("plc.client.communication.cableway_plc")
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    config = CablewayPLCConfig(
        host="127.0.0.1",
        port=1502,
        unit_id=2,
        even_byte_is_high=False,
        float_word_order="little",
        float_byte_order="little",
    )
    CablewayPLC(config)

    logger.removeHandler(handler)

    messages = [record.getMessage() for record in handler.records]
    joined = "\n".join(messages)
    assert "even_byte_is_high" in joined
    assert "float_word_order" in joined
    assert "float_byte_order" in joined
