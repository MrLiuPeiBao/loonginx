from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from app.services.cableway_specs import (
    CONTROL_COMMANDS,
    FAULT_BITS,
    HEARTBEAT_VALUES,
    STATUS_BITS,
    TOTAL_FAULT_KEY,
    VB_FAULT_WINDOW_END,
    VB_FAULT_WINDOW_START,
    VB_STATUS_WINDOW_END,
    VB_STATUS_WINDOW_START,
    VD_CURRENT_POSITION,
    VW_COMMAND,
    VW_CURRENT_TASK,
    VW_ESTOP,
    VW_HEARTBEAT,
)
from app.services.modbus_tcp import ModbusTCPClient, ModbusTCPConfig, ModbusTCPError
from app.services.plc_logging import get_plc_logger
from app.services.plc_timing import emit_plc_timing


logger = get_plc_logger(__name__)


class CablewayPLCYieldToUrgentWork(RuntimeError):
    """Raised when polling should yield to a queued control command."""


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


def _byte_window_to_register_range(start_byte: int, end_byte: int) -> tuple[int, int]:
    if start_byte < 0 or end_byte < 0 or end_byte < start_byte:
        raise ValueError(f"Invalid byte window start={start_byte} end={end_byte}")
    start_word = int(start_byte) if (int(start_byte) % 2 == 0) else (int(start_byte) - 1)
    end_word = int(end_byte) if (int(end_byte) % 2 == 0) else (int(end_byte) - 1)
    return _vw_to_register(start_word), _vw_to_register(end_word)


def _control_command_requires_reset(command_code: int) -> bool:
    return int(command_code) not in {401, 402}


def _decode_float32_from_registers(
    registers: list[int],
    *,
    word_order: str = "big",
    byte_order: str = "big",
) -> float:
    if len(registers) != 2:
        raise ValueError(f"Float32 requires 2 registers, got {len(registers)}")
    words = [struct.pack(">H", int(value) & 0xFFFF) for value in registers]
    if word_order == "little":
        words = [words[1], words[0]]
    elif word_order != "big":
        raise ValueError(f"Unsupported float word_order={word_order}")

    normalized: list[bytes] = []
    for word in words:
        if byte_order == "little":
            normalized.append(word[::-1])
        elif byte_order == "big":
            normalized.append(word)
        else:
            raise ValueError(f"Unsupported float byte_order={byte_order}")
    return float(struct.unpack(">f", b"".join(normalized))[0])


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


def _build_register_map(start_register: int, values: list[int]) -> dict[int, int]:
    return {int(start_register) + offset: int(value) for offset, value in enumerate(values)}


@dataclass(frozen=True)
class CablewayPLCConfig:
    host: str = "192.168.2.1"
    port: int = 502
    unit_id: int = 1
    timeout: float = 0.5
    connect_timeout: float = 2.0
    status_poll_interval: float = 0.5
    heartbeat_interval: float = 1.0
    command_pulse_seconds: float = 0.1
    even_byte_is_high: bool = True
    float_word_order: str = "big"
    float_byte_order: str = "big"


@dataclass(frozen=True)
class CablewayCommandExecution:
    response_payload: Dict[str, Any]
    status_payload: Optional[Dict[str, Any]] = None
    frames: tuple[Dict[str, Any], ...] = ()


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
        self._trace_context: Dict[str, Any] = {}
        logger.info(
            "Server Cableway PLC init host=%s port=%s unit_id=%s timeout=%s connect_timeout=%s "
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

    @property
    def heartbeat_interval(self) -> float:
        return float(self._config.heartbeat_interval)

    @property
    def host(self) -> str:
        return str(self._config.host)

    def close(self) -> None:
        logger.info("Server Cableway PLC close requested")
        self._client.close()

    def set_trace_sink(self, sink) -> None:
        self._client.set_trace_sink(sink)

    def set_trace_context(self, context: Optional[Dict[str, Any]]) -> None:
        self._trace_context = dict(context or {})
        self._client.set_trace_context(self._trace_context)

    def ensure_connected(self) -> None:
        self._emit_timing(
            "plc.ensure_connected_start",
            socket_connected_before=bool(getattr(self._client, "is_connected", False)),
            host=self._config.host,
            port=self._config.port,
            unit_id=self._config.unit_id,
        )
        logger.debug("Server Cableway PLC ensure connection")
        self._client.connect()
        self._emit_timing(
            "plc.ensure_connected_end",
            socket_connected_before=True,
            host=self._config.host,
            port=self._config.port,
            unit_id=self._config.unit_id,
        )

    def poll_status_and_heartbeat(
        self,
        *,
        should_yield: Optional[Callable[[], bool]] = None,
    ) -> Optional[dict]:
        logger.debug("Server Cableway PLC poll start")
        try:
            self.ensure_connected()
            self._raise_if_should_yield(should_yield, stage="heartbeat")
            try:
                self._maybe_send_heartbeat()
            except ModbusTCPError as exc:
                logger.warning("Server Cableway PLC heartbeat failed: %s", exc)
            status = self.read_status(should_yield=should_yield)
            logger.debug("Server Cableway PLC poll complete status=%s", bool(status))
            return status
        except CablewayPLCYieldToUrgentWork:
            raise
        except ModbusTCPError as exc:
            logger.error("Server Cableway PLC poll failed: %s", exc)
        except Exception as exc:
            logger.exception("Server Cableway PLC poll unexpected error: %s", exc)
        return None

    def send_control_command(self, command_code: int, *, pulse: bool = True) -> None:
        if int(command_code) not in CONTROL_COMMANDS:
            raise ValueError(f"Unsupported control command_code={command_code}")
        effective_pulse = _control_command_requires_reset(int(command_code))
        register_address = _vw_to_register(VW_COMMAND)
        self._emit_timing(
            "plc.control_start",
            register=register_address,
            command_code=int(command_code),
            pulse=effective_pulse,
            command_type="control",
        )
        logger.info(
            "Server Cableway PLC control command code=%s name=%s pulse=%s requested_pulse=%s",
            int(command_code),
            CONTROL_COMMANDS.get(int(command_code)),
            effective_pulse,
            pulse,
        )
        self._write_pulse(
            register_address,
            int(command_code),
            pulse=effective_pulse,
            command_type="control",
            command_code=int(command_code),
        )

    def send_estop(self, *, pulse: bool = True) -> None:
        register_address = _vw_to_register(VW_ESTOP)
        self._emit_timing(
            "plc.control_start",
            register=register_address,
            pulse=bool(pulse),
            command_type="estop",
        )
        logger.info("Server Cableway PLC estop command pulse=%s", pulse)
        self._write_pulse(
            register_address,
            1,
            pulse=pulse,
            command_type="estop",
        )

    def read_status(
        self,
        *,
        should_yield: Optional[Callable[[], bool]] = None,
    ) -> dict:
        self._emit_timing("plc.read_status_start")
        logger.debug("Server Cableway PLC read status start")
        self.ensure_connected()

        motion = self.read_motion_values(should_yield=should_yield)
        current_task_code = self.read_current_task_code(should_yield=should_yield)
        base_bits = self.read_status_bits(should_yield=should_yield)
        total_fault = bool(base_bits.get(TOTAL_FAULT_KEY))

        faults: Dict[str, bool] = {TOTAL_FAULT_KEY: total_fault}
        fault_query_performed = False
        fault_query_error: Optional[str] = None
        if total_fault:
            fault_query_performed = True
            try:
                self._raise_if_should_yield(should_yield, stage="fault_details")
                faults = self.read_fault_details(should_yield=should_yield)
                faults[TOTAL_FAULT_KEY] = True
            except CablewayPLCYieldToUrgentWork:
                raise
            except Exception as exc:
                fault_query_error = str(exc)
                logger.warning("Server Cableway PLC fault detail query failed: %s", exc)

        active_faults = [
            FAULT_BITS[key].name
            for key, enabled in faults.items()
            if enabled and key in FAULT_BITS
        ]
        outputs = {
            "zt_home_done": bool(base_bits.get("zt_home_done")),
            "zt_position_done": bool(base_bits.get("zt_position_done")),
        }

        return {
            "timestamp": _now_iso(),
            "ts": _now_ts(),
            "plc_host": self._config.host,
            **motion,
            "current_task_code": int(current_task_code),
            "total_fault": total_fault,
            "home_completed": outputs["zt_home_done"],
            "positioning_completed": outputs["zt_position_done"],
            "faults": faults,
            "outputs": outputs,
            "active_faults": active_faults,
            "fault_query_performed": fault_query_performed,
            "fault_query_error": fault_query_error,
        }

    def query_faults(
        self,
        *,
        should_yield: Optional[Callable[[], bool]] = None,
    ) -> dict:
        return self.read_status(should_yield=should_yield)

    def read_fault_details(
        self,
        *,
        should_yield: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, bool]:
        self.ensure_connected()
        self._raise_if_should_yield(should_yield, stage="fault_window")
        self._emit_timing("plc.read_fault_window_start")
        start_register, end_register = _byte_window_to_register_range(
            VB_FAULT_WINDOW_START,
            VB_FAULT_WINDOW_END,
        )
        fault_registers = self._client.read_holding_registers(
            start_register,
            end_register - start_register + 1,
        )
        reg_map = _build_register_map(start_register, fault_registers)
        decoded = self._decode_bits(reg_map, FAULT_BITS)
        self._emit_timing(
            "plc.read_fault_window_end",
            register=start_register,
            count=end_register - start_register + 1,
            fault_query_performed=True,
        )
        return decoded

    def read_motion_values(
        self,
        *,
        should_yield: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, float]:
        self.ensure_connected()
        self._raise_if_should_yield(should_yield, stage="motion")
        self._emit_timing("plc.read_motion_start", register=_vd_to_register(VD_CURRENT_POSITION), count=6)
        motion_registers = self._client.read_holding_registers(_vd_to_register(VD_CURRENT_POSITION), 6)
        decoded = {
            "current_position_m": _decode_float32_from_registers(
                motion_registers[0:2],
                word_order=self._config.float_word_order,
                byte_order=self._config.float_byte_order,
            ),
            "current_speed_mps": _decode_float32_from_registers(
                motion_registers[2:4],
                word_order=self._config.float_word_order,
                byte_order=self._config.float_byte_order,
            ),
            "target_position_m": _decode_float32_from_registers(
                motion_registers[4:6],
                word_order=self._config.float_word_order,
                byte_order=self._config.float_byte_order,
            ),
        }
        self._emit_timing("plc.read_motion_end", register=_vd_to_register(VD_CURRENT_POSITION), count=6)
        return decoded

    def read_current_task_code(
        self,
        *,
        should_yield: Optional[Callable[[], bool]] = None,
    ) -> int:
        self.ensure_connected()
        self._raise_if_should_yield(should_yield, stage="task")
        register = _vw_to_register(VW_CURRENT_TASK)
        self._emit_timing("plc.read_task_start", register=register, count=1)
        value = int(self._client.read_holding_registers(register, 1)[0])
        self._emit_timing("plc.read_task_end", register=register, count=1)
        return value

    def read_status_bits(
        self,
        *,
        should_yield: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, bool]:
        self.ensure_connected()
        self._raise_if_should_yield(should_yield, stage="status_bits")
        status_start_register, status_end_register = _byte_window_to_register_range(
            VB_STATUS_WINDOW_START,
            VB_STATUS_WINDOW_END,
        )
        count = status_end_register - status_start_register + 1
        self._emit_timing("plc.read_status_bits_start", register=status_start_register, count=count)
        status_registers = self._client.read_holding_registers(
            status_start_register,
            count,
        )
        status_map = _build_register_map(status_start_register, status_registers)
        decoded = self._decode_bits(status_map, STATUS_BITS)
        self._emit_timing("plc.read_status_bits_end", register=status_start_register, count=count)
        return decoded

    def _decode_bits(self, reg_map: dict[int, int], specs: Dict[str, Any]) -> Dict[str, bool]:
        decoded: Dict[str, bool] = {}
        for key, spec in specs.items():
            word_base = spec.byte_address if (spec.byte_address % 2 == 0) else (spec.byte_address - 1)
            register_addr = _vw_to_register(word_base)
            decoded[key] = _decode_bit_from_register(
                reg_map.get(register_addr, 0),
                spec.byte_address,
                spec.bit_index,
                even_byte_is_high=self._config.even_byte_is_high,
            )
        return decoded

    def _maybe_send_heartbeat(self) -> None:
        now = time.monotonic()
        interval = float(self._config.heartbeat_interval)
        if interval <= 0:
            return
        if now - self._last_heartbeat_sent < interval:
            logger.debug("Server Cableway PLC heartbeat skipped interval=%s", interval)
            return
        value = HEARTBEAT_VALUES[self._heartbeat_index % len(HEARTBEAT_VALUES)]
        self._heartbeat_index += 1
        self._client.write_single_register(_vw_to_register(VW_HEARTBEAT), int(value))
        self._last_heartbeat_sent = now

    def _write_pulse(
        self,
        register_address: int,
        value: int,
        *,
        pulse: bool,
        command_type: str,
        command_code: Optional[int] = None,
    ) -> None:
        self.ensure_connected()
        self._client.write_single_register(int(register_address), int(value))
        self._emit_timing(
            "plc.control_primary_write_done",
            register=register_address,
            command_code=command_code,
            pulse=pulse,
            command_type=command_type,
        )
        if not pulse:
            return
        self._emit_timing(
            "plc.pulse_sleep_start",
            register=register_address,
            command_code=command_code,
            pulse=pulse,
            command_type=command_type,
        )
        time.sleep(max(0.0, float(self._config.command_pulse_seconds)))
        self._emit_timing(
            "plc.pulse_sleep_end",
            register=register_address,
            command_code=command_code,
            pulse=pulse,
            command_type=command_type,
        )
        try:
            self._client.write_single_register(int(register_address), 0)
        except ModbusTCPError:
            logger.warning("Server Cableway PLC pulse reset failed, retrying once", exc_info=True)
            self._client.close()
            self._client.write_single_register(int(register_address), 0)
        self._emit_timing(
            "plc.control_reset_write_done",
            register=register_address,
            command_code=command_code,
            pulse=pulse,
            command_type=command_type,
        )

    def _raise_if_should_yield(
        self,
        should_yield: Optional[Callable[[], bool]],
        *,
        stage: str,
    ) -> None:
        if should_yield is not None and should_yield():
            logger.debug("Server Cableway PLC yield to urgent control stage=%s", stage)
            raise CablewayPLCYieldToUrgentWork(stage)

    def _emit_timing(self, stage: str, **fields: Any) -> None:
        context_fields = {
            key: value
            for key, value in self._trace_context.items()
            if key not in {"request_id", "trace_id"}
        }
        merged_fields = dict(context_fields)
        merged_fields.update(fields)
        emit_plc_timing(
            stage,
            request_id=str(self._trace_context.get("request_id") or ""),
            trace_id=str(self._trace_context.get("trace_id") or ""),
            **merged_fields,
        )


from app.services.legacy_cableway_plc_service import CablewayPLCService, LegacyCablewayPLCService  # noqa: E402
