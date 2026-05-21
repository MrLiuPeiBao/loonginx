from __future__ import annotations

import socket
import struct
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.services.plc_logging import get_plc_logger
from app.services.plc_timing import emit_plc_timing


logger = get_plc_logger(__name__)

FrameTraceSink = Callable[[Dict[str, Any]], None]


class ModbusTCPError(RuntimeError):
    """Raised when a Modbus TCP request fails."""


@dataclass(frozen=True)
class ModbusTCPConfig:
    host: str
    port: int = 502
    unit_id: int = 1
    timeout: float = 2.0
    connect_timeout: float = 2.0


def _format_hex(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in bytes(data or b""))


def _regs_to_text(values: List[int]) -> str:
    if not values:
        return ""
    preview = ", ".join(str(value) for value in values[:8])
    if len(values) > 8:
        preview += f" ... ({len(values)} regs)"
    return preview


def _parse_frame(frame: bytes, *, direction: str) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "direction": direction,
        "tx_id": "",
        "unit_id": "",
        "function": "",
        "operation": "",
        "address": "",
        "quantity": "",
        "values": "",
        "raw_hex": _format_hex(frame),
    }
    if len(frame) < 8:
        entry["operation"] = "Short frame"
        return entry

    tx_id = int.from_bytes(frame[0:2], byteorder="big")
    unit_id = frame[6]
    function_code = frame[7]
    entry["tx_id"] = str(tx_id)
    entry["unit_id"] = str(unit_id)
    entry["function"] = f"0x{function_code:02X}"

    if function_code & 0x80:
        base_code = function_code & 0x7F
        entry["operation"] = f"Exception FC 0x{base_code:02X}"
        if len(frame) > 8:
            entry["values"] = f"exception_code={frame[8]}"
        return entry

    if function_code == 0x03:
        entry["operation"] = "Read Holding Registers"
        if direction == "TX" and len(frame) >= 12:
            entry["address"] = str(int.from_bytes(frame[8:10], byteorder="big"))
            entry["quantity"] = str(int.from_bytes(frame[10:12], byteorder="big"))
        elif direction == "RX" and len(frame) >= 9:
            byte_count = frame[8]
            payload = frame[9 : 9 + byte_count]
            values = [
                int.from_bytes(payload[index : index + 2], byteorder="big")
                for index in range(0, len(payload), 2)
                if len(payload[index : index + 2]) == 2
            ]
            entry["quantity"] = str(len(values))
            entry["values"] = _regs_to_text(values)
        return entry

    if function_code == 0x06:
        entry["operation"] = "Write Single Register"
        if len(frame) >= 12:
            entry["address"] = str(int.from_bytes(frame[8:10], byteorder="big"))
            entry["quantity"] = "1"
            entry["values"] = str(int.from_bytes(frame[10:12], byteorder="big"))
        return entry

    if function_code == 0x10:
        entry["operation"] = "Write Multiple Registers"
        if direction == "TX" and len(frame) >= 13:
            address = int.from_bytes(frame[8:10], byteorder="big")
            quantity = int.from_bytes(frame[10:12], byteorder="big")
            byte_count = frame[12]
            payload = frame[13 : 13 + byte_count]
            values = [
                int.from_bytes(payload[index : index + 2], byteorder="big")
                for index in range(0, len(payload), 2)
                if len(payload[index : index + 2]) == 2
            ]
            entry["address"] = str(address)
            entry["quantity"] = str(quantity)
            entry["values"] = _regs_to_text(values)
        elif direction == "RX" and len(frame) >= 12:
            entry["address"] = str(int.from_bytes(frame[8:10], byteorder="big"))
            entry["quantity"] = str(int.from_bytes(frame[10:12], byteorder="big"))
        return entry

    entry["operation"] = f"Function 0x{function_code:02X}"
    return entry


def _parse_pdu_info(pdu: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "fc": pdu[0] if pdu else None,
        "address": None,
        "quantity": None,
        "value": None,
    }
    if not pdu:
        return info
    function_code = int(pdu[0])
    if function_code == 0x03 and len(pdu) >= 5:
        info["address"] = int.from_bytes(pdu[1:3], byteorder="big")
        info["quantity"] = int.from_bytes(pdu[3:5], byteorder="big")
    elif function_code == 0x06 and len(pdu) >= 5:
        info["address"] = int.from_bytes(pdu[1:3], byteorder="big")
        info["quantity"] = 1
        info["value"] = int.from_bytes(pdu[3:5], byteorder="big")
    elif function_code == 0x10 and len(pdu) >= 5:
        info["address"] = int.from_bytes(pdu[1:3], byteorder="big")
        info["quantity"] = int.from_bytes(pdu[3:5], byteorder="big")
    return info


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ModbusTCPError("Socket closed by peer")
        data.extend(chunk)
    return bytes(data)


class ModbusTCPClient:
    """Minimal Modbus TCP client for holding register read/write (FC03/06/16)."""

    def __init__(self, config: ModbusTCPConfig):
        self._config = config
        self._sock: Optional[socket.socket] = None
        self._lock = threading.RLock()
        self._transaction_id = 0
        self._trace_sink: Optional[FrameTraceSink] = None
        self._trace_context: Dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        with self._lock:
            if self._sock is not None:
                logger.debug(
                    "Modbus TCP already connected host=%s port=%s",
                    self._config.host,
                    self._config.port,
                )
                return
            emit_plc_timing(
                "modbus.connect_start",
                socket_connected_before=False,
                host=self._config.host,
                port=self._config.port,
                unit_id=self._config.unit_id,
                **self._trace_context,
            )
            logger.info(
                "Modbus TCP connecting host=%s port=%s unit_id=%s",
                self._config.host,
                self._config.port,
                self._config.unit_id,
            )
            try:
                sock = socket.create_connection(
                    (self._config.host, int(self._config.port)),
                    timeout=float(self._config.connect_timeout),
                )
            except OSError as exc:
                logger.warning(
                    "Modbus TCP connect failed host=%s port=%s error=%s",
                    self._config.host,
                    self._config.port,
                    exc,
                )
                emit_plc_timing(
                    "modbus.request_error",
                    socket_connected_before=False,
                    host=self._config.host,
                    port=self._config.port,
                    unit_id=self._config.unit_id,
                    error=str(exc),
                    **self._trace_context,
                )
                raise ModbusTCPError(f"Modbus TCP connect failed: {exc}") from exc
            sock.settimeout(float(self._config.timeout))
            self._sock = sock
            emit_plc_timing(
                "modbus.connect_end",
                socket_connected_before=False,
                host=self._config.host,
                port=self._config.port,
                unit_id=self._config.unit_id,
                **self._trace_context,
            )
            logger.info(
                "Modbus TCP connected host=%s port=%s",
                self._config.host,
                self._config.port,
            )

    def close(self) -> None:
        with self._lock:
            if self._sock is None:
                return
            try:
                self._sock.close()
            finally:
                self._sock = None
                logger.info(
                    "Modbus TCP connection closed host=%s port=%s",
                    self._config.host,
                    self._config.port,
                )

    def set_trace_sink(self, sink: Optional[FrameTraceSink]) -> None:
        with self._lock:
            self._trace_sink = sink

    def set_trace_context(self, context: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            self._trace_context = dict(context or {})

    def read_holding_registers(self, address: int, count: int) -> List[int]:
        if count <= 0:
            raise ValueError(f"Invalid register count={count}")
        logger.debug(
            "Modbus read holding registers address=%s count=%s",
            address,
            count,
        )
        pdu = struct.pack(">BHH", 3, int(address) & 0xFFFF, int(count) & 0xFFFF)
        resp = self._request(pdu)
        function = resp[0]
        if function & 0x80:
            code = resp[1] if len(resp) > 1 else None
            raise ModbusTCPError(f"Read holding registers exception code={code}")
        if function != 3:
            raise ModbusTCPError(f"Unexpected function code={function}")
        if len(resp) < 2:
            raise ModbusTCPError("Malformed response (missing byte count)")
        byte_count = resp[1]
        expected_bytes = int(count) * 2
        if byte_count != expected_bytes or len(resp) != 2 + expected_bytes:
            raise ModbusTCPError(
                f"Malformed response byte_count={byte_count} expected={expected_bytes} length={len(resp)}"
            )
        values = list(struct.unpack(f">{count}H", resp[2:]))
        logger.debug(
            "Modbus read response address=%s count=%s values=%s",
            address,
            count,
            values,
        )
        return values

    def write_single_register(self, address: int, value: int) -> None:
        logger.debug(
            "Modbus write single register address=%s value=%s",
            address,
            value,
        )
        pdu = struct.pack(">BHH", 6, int(address) & 0xFFFF, int(value) & 0xFFFF)
        resp = self._request(pdu)
        function = resp[0]
        if function & 0x80:
            code = resp[1] if len(resp) > 1 else None
            if code == 1:
                logger.warning(
                    "Modbus FC06 rejected with exception code=1, fallback to FC16 address=%s value=%s",
                    address,
                    value,
                )
                self.write_multiple_registers(address, [value])
                return
            raise ModbusTCPError(f"Write single register exception code={code}")
        if resp != pdu:
            raise ModbusTCPError("Write single register echo mismatch")
        logger.debug(
            "Modbus write single register success address=%s value=%s",
            address,
            value,
        )

    def write_multiple_registers(self, address: int, values: List[int]) -> None:
        if not values:
            raise ValueError("Values must not be empty")
        count = len(values)
        logger.debug(
            "Modbus write multiple registers address=%s count=%s values=%s",
            address,
            count,
            values,
        )
        byte_count = count * 2
        header = struct.pack(
            ">BHHB",
            16,
            int(address) & 0xFFFF,
            count & 0xFFFF,
            byte_count & 0xFF,
        )
        payload = struct.pack(f">{count}H", *(int(v) & 0xFFFF for v in values))
        pdu = header + payload
        resp = self._request(pdu)
        function = resp[0]
        if function & 0x80:
            code = resp[1] if len(resp) > 1 else None
            raise ModbusTCPError(f"Write multiple registers exception code={code}")
        if len(resp) != 5:
            raise ModbusTCPError("Malformed write multiple registers response")
        _, resp_addr, resp_count = struct.unpack(">BHH", resp)
        if resp_addr != (int(address) & 0xFFFF) or resp_count != count:
            raise ModbusTCPError("Write multiple registers response mismatch")
        logger.debug(
            "Modbus write multiple registers success address=%s count=%s",
            address,
            count,
        )

    def _request(self, pdu: bytes) -> bytes:
        with self._lock:
            socket_connected_before = self._sock is not None
            if self._sock is None:
                self.connect()
            if self._sock is None:
                raise ModbusTCPError("Not connected")

            self._transaction_id = (self._transaction_id + 1) & 0xFFFF
            tid = self._transaction_id
            unit_id = int(self._config.unit_id) & 0xFF
            length = len(pdu) + 1
            mbap = struct.pack(">HHH", tid, 0, length) + struct.pack(">B", unit_id)
            request_frame = mbap + pdu
            pdu_info = _parse_pdu_info(pdu)
            common_fields = {
                "modbus_tx_id": tid,
                "fc": pdu_info.get("fc"),
                "address": pdu_info.get("address"),
                "quantity": pdu_info.get("quantity"),
                "unit_id": unit_id,
                "socket_connected_before": socket_connected_before,
                **self._trace_context,
            }

            try:
                emit_plc_timing("modbus.request_start", **common_fields)
                logger.debug(
                    "Modbus request tx=%s unit_id=%s length=%s",
                    tid,
                    unit_id,
                    length,
                )
                logger.debug("Modbus TX frame=%s", _format_hex(request_frame))
                self._emit_trace(direction="TX", frame=request_frame)
                self._sock.sendall(request_frame)
                emit_plc_timing("modbus.tx_send", **common_fields)
                header = _recv_exact(self._sock, 7)
                emit_plc_timing("modbus.rx_header", response_length=len(header), **common_fields)
                resp_tid, proto, resp_length, resp_unit = struct.unpack(">HHHB", header)
                if proto != 0:
                    raise ModbusTCPError(f"Unexpected protocol id={proto}")
                if resp_tid != tid:
                    raise ModbusTCPError(f"Transaction id mismatch resp={resp_tid} req={tid}")
                if resp_unit != unit_id:
                    raise ModbusTCPError(f"Unit id mismatch resp={resp_unit} req={unit_id}")
                remaining = int(resp_length) - 1
                if remaining <= 0:
                    raise ModbusTCPError(f"Invalid MBAP length={resp_length}")
                payload = _recv_exact(self._sock, remaining)
                emit_plc_timing("modbus.rx_payload", response_length=len(payload), **common_fields)
                response_frame = header + payload
                logger.debug(
                    "Modbus response tx=%s unit_id=%s length=%s",
                    resp_tid,
                    resp_unit,
                    resp_length,
                )
                logger.debug("Modbus RX frame=%s", _format_hex(response_frame))
                self._emit_trace(direction="RX", frame=response_frame)
                emit_plc_timing("modbus.request_end", response_length=len(payload), **common_fields)
                return payload
            except (OSError, socket.timeout) as exc:
                self.close()
                logger.warning("Modbus TCP request failed: %s", exc)
                emit_plc_timing("modbus.request_error", error=str(exc), **common_fields)
                raise ModbusTCPError(f"Modbus TCP request failed: {exc}") from exc

    def _emit_trace(self, *, direction: str, frame: bytes) -> None:
        sink = self._trace_sink
        if sink is None:
            return
        try:
            entry = _parse_frame(frame, direction=direction)
            entry.update({key: value for key, value in self._trace_context.items() if value is not None})
            sink(entry)
        except Exception:
            logger.debug("Failed to emit Modbus frame trace", exc_info=True)
