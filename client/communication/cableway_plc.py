from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from communication.cableway_specs import (
    CONTROL_COMMANDS,
    FAULT_BITS,
    FLOAT_PARAMS,
    HEARTBEAT_VALUES,
    OUTPUT_BITS,
    VW_COMMAND,
    VW_ESTOP,
    VW_HEARTBEAT,
)
from communication.modbus_tcp import ModbusTCPClient, ModbusTCPConfig, ModbusTCPError
from communication.plc_logging import get_plc_logger

logger = get_plc_logger(__name__)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _now_ts() -> float:
    return datetime.now().astimezone().timestamp()


def _vw_to_register(vw_address: int) -> int:
    if vw_address < 0:
        raise ValueError(f"Invalid VW address={vw_address}")
    return int(vw_address) // 2


def _vd_to_register(vd_address: int) -> int:
    if vd_address < 0:
        raise ValueError(f"Invalid VD address={vd_address}")
    return int(vd_address) // 2


def _encode_float32_to_registers(
    value: float,
    *,
    word_order: str = "big",
    byte_order: str = "big",
) -> list[int]:
    raw = struct.pack(">f", float(value))
    word0 = raw[0:2]
    word1 = raw[2:4]
    if byte_order == "little":
        word0 = word0[::-1]
        word1 = word1[::-1]
    elif byte_order != "big":
        raise ValueError(f"Unsupported float byte_order={byte_order}")
    words = [word0, word1]
    if word_order == "little":
        words = [word1, word0]
    elif word_order != "big":
        raise ValueError(f"Unsupported float word_order={word_order}")
    return [struct.unpack(">H", word)[0] for word in words]


def _decode_bit_from_register(
    register_value: int,
    byte_address: int,
    bit_index: int,
    *,
    even_byte_is_high: bool,
) -> bool:
    if not (0 <= bit_index <= 7):
        raise ValueError(f"Invalid bit_index={bit_index}")

    is_even = (int(byte_address) % 2) == 0
    high_byte = (int(register_value) >> 8) & 0xFF
    low_byte = int(register_value) & 0xFF
    if even_byte_is_high:
        target_byte = high_byte if is_even else low_byte
    else:
        target_byte = low_byte if is_even else high_byte
    return bool((target_byte >> int(bit_index)) & 0x01)


@dataclass(frozen=True)
class CablewayPLCConfig:
    host: str = "192.168.2.1"
    port: int = 502
    unit_id: int = 1
    timeout: float = 2.0
    connect_timeout: float = 2.0
    status_poll_interval: float = 0.5
    heartbeat_interval: float = 1.0
    command_pulse_seconds: float = 0.1
    even_byte_is_high: bool = True
    float_word_order: str = "big"
    float_byte_order: str = "big"


class CablewayPLC:
    """Cableway PLC integration via Modbus TCP (Siemens S7-200 SMART)."""

    def __init__(self, config: CablewayPLCConfig):
        self._config = config
        self._client = ModbusTCPClient(
            ModbusTCPConfig(
                host=config.host,
                port=config.port,
                unit_id=config.unit_id,
                timeout=config.timeout,
                connect_timeout=config.connect_timeout,
            )
        )
        self._heartbeat_index = 0
        self._last_heartbeat_sent = 0.0
        logger.info(
            "Cableway PLC init host=%s port=%s unit_id=%s timeout=%s connect_timeout=%s "
            "status_poll_interval=%s heartbeat_interval=%s even_byte_is_high=%s "
            "float_word_order=%s float_byte_order=%s",
            config.host,
            config.port,
            config.unit_id,
            config.timeout,
            config.connect_timeout,
            config.status_poll_interval,
            config.heartbeat_interval,
            config.even_byte_is_high,
            config.float_word_order,
            config.float_byte_order,
        )

    @property
    def status_poll_interval(self) -> float:
        return float(self._config.status_poll_interval)

    def close(self) -> None:
        logger.info("Cableway PLC close requested")
        self._client.close()

    def ensure_connected(self) -> None:
        logger.debug("Cableway PLC ensure connection")
        self._client.connect()

    # Public operations -------------------------------------------------
    def poll_status_and_heartbeat(self) -> Optional[dict]:
        logger.debug("Cableway PLC poll start")
        try:
            self.ensure_connected()
            try:
                self._maybe_send_heartbeat()
            except ModbusTCPError as exc:
                logger.warning("Cableway PLC heartbeat failed: %s", exc)
            status = self.read_status()
            logger.debug("Cableway PLC poll complete status=%s", bool(status))
            return status
        except ModbusTCPError as exc:
            logger.error("Cableway PLC poll failed: %s", exc)
        except Exception as exc:
            logger.exception("Cableway PLC poll unexpected error: %s", exc)
        return None

    def send_control_command(self, command_code: int, *, pulse: bool = True) -> None:
        if int(command_code) not in CONTROL_COMMANDS:
            raise ValueError(f"Unsupported control command_code={command_code}")
        logger.info(
            "Cableway PLC control command code=%s name=%s pulse=%s",
            int(command_code),
            CONTROL_COMMANDS.get(int(command_code)),
            pulse,
        )
        self._write_pulse(_vw_to_register(VW_COMMAND), int(command_code), pulse=pulse)

    def send_estop(self, *, pulse: bool = True) -> None:
        logger.info("Cableway PLC estop command pulse=%s", pulse)
        self._write_pulse(_vw_to_register(VW_ESTOP), 1, pulse=pulse)

    def set_params(self, params: Dict[str, float]) -> None:
        if not params:
            raise ValueError("params must not be empty")
        logger.info("Cableway PLC set params count=%s", len(params))
        for key, value in params.items():
            spec = FLOAT_PARAMS.get(key)
            if not spec:
                raise ValueError(f"Unknown param key={key}")
            numeric = float(value)
            if spec.min_value is not None and numeric < spec.min_value:
                raise ValueError(f"Param {key} below min {spec.min_value}: {numeric}")
            if spec.max_value is not None and numeric > spec.max_value:
                raise ValueError(f"Param {key} above max {spec.max_value}: {numeric}")
            registers = _encode_float32_to_registers(
                numeric,
                word_order=self._config.float_word_order,
                byte_order=self._config.float_byte_order,
            )
            logger.debug(
                "Cableway PLC write param key=%s value=%s vd_address=%s registers=%s",
                key,
                numeric,
                spec.vd_address,
                registers,
            )
            self._client.write_multiple_registers(_vd_to_register(spec.vd_address), registers)

    def read_status(self) -> dict:
        logger.debug("Cableway PLC read status start")
        reg_2400 = self._client.read_holding_registers(_vw_to_register(2400), 1)[0]
        start_register = _vw_to_register(2810)
        end_register = _vw_to_register(2888)
        regs_2810_2888 = self._client.read_holding_registers(
            start_register,
            end_register - start_register + 1,
        )
        logger.debug(
            "Cableway PLC read registers 2400=%s 2810-2888 count=%s",
            reg_2400,
            len(regs_2810_2888),
        )
        reg_map = {
            _vw_to_register(2400): reg_2400,
        }
        for offset, value in enumerate(regs_2810_2888):
            reg_map[start_register + offset] = value

        faults: Dict[str, bool] = {}
        for key, spec in FAULT_BITS.items():
            word_base = spec.byte_address if (spec.byte_address % 2 == 0) else (spec.byte_address - 1)
            register_addr = _vw_to_register(word_base)
            faults[key] = _decode_bit_from_register(
                reg_map.get(register_addr, 0),
                spec.byte_address,
                spec.bit_index,
                even_byte_is_high=self._config.even_byte_is_high,
            )

        outputs: Dict[str, bool] = {}
        for key, spec in OUTPUT_BITS.items():
            word_base = spec.byte_address if (spec.byte_address % 2 == 0) else (spec.byte_address - 1)
            register_addr = _vw_to_register(word_base)
            outputs[key] = _decode_bit_from_register(
                reg_map.get(register_addr, 0),
                spec.byte_address,
                spec.bit_index,
                even_byte_is_high=self._config.even_byte_is_high,
            )

        active_faults = [FAULT_BITS[key].name for key, enabled in faults.items() if enabled]
        active_outputs = [OUTPUT_BITS[key].name for key, enabled in outputs.items() if enabled]
        logger.debug(
            "Cableway PLC status decoded active_faults=%s active_outputs=%s",
            active_faults,
            active_outputs,
        )
        return {
            "timestamp": _now_iso(),
            "ts": _now_ts(),
            "plc_host": self._config.host,
            "faults": faults,
            "outputs": outputs,
            "active_faults": active_faults,
        }

    # Internal ---------------------------------------------------------
    def _maybe_send_heartbeat(self) -> None:
        now = time.monotonic()
        interval = float(self._config.heartbeat_interval)
        if now - self._last_heartbeat_sent < interval:
            logger.debug("Cableway PLC heartbeat skipped interval=%s", interval)
            return
        value = HEARTBEAT_VALUES[self._heartbeat_index % len(HEARTBEAT_VALUES)]
        self._heartbeat_index += 1
        logger.debug(
            "Cableway PLC heartbeat send value=%s register=%s",
            value,
            _vw_to_register(VW_HEARTBEAT),
        )
        self._client.write_single_register(_vw_to_register(VW_HEARTBEAT), int(value))
        self._last_heartbeat_sent = now

    def _write_pulse(self, register_address: int, value: int, *, pulse: bool) -> None:
        logger.debug(
            "Cableway PLC write pulse register=%s value=%s pulse=%s",
            register_address,
            value,
            pulse,
        )
        self.ensure_connected()
        self._client.write_single_register(int(register_address), int(value))
        if not pulse:
            return
        time.sleep(max(0.0, float(self._config.command_pulse_seconds)))
        try:
            self._client.write_single_register(int(register_address), 0)
            logger.debug(
                "Cableway PLC pulse reset register=%s value=0",
                register_address,
            )
        except ModbusTCPError:
            logger.warning("Pulse reset failed, retrying once", exc_info=True)
            self._client.close()
            self._client.write_single_register(int(register_address), 0)
            logger.debug(
                "Cableway PLC pulse reset retry success register=%s value=0",
                register_address,
            )
