from __future__ import annotations

import socket
import struct
import threading
from dataclasses import dataclass
from typing import List, Optional

from communication.plc_logging import get_plc_logger


logger = get_plc_logger(__name__)


class ModbusTCPError(RuntimeError):
    """Raised when a Modbus TCP request fails."""


@dataclass(frozen=True)
class ModbusTCPConfig:
    host: str
    port: int = 502
    unit_id: int = 1
    timeout: float = 2.0
    connect_timeout: float = 2.0


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
                logger.error(
                    "Modbus TCP connect failed host=%s port=%s error=%s",
                    self._config.host,
                    self._config.port,
                    exc,
                )
                raise ModbusTCPError(f"Modbus TCP connect failed: {exc}") from exc
            sock.settimeout(float(self._config.timeout))
            self._sock = sock
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
        header = struct.pack(">BHHB", 16, int(address) & 0xFFFF, count & 0xFFFF, byte_count & 0xFF)
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

    # Internal ---------------------------------------------------------
    def _request(self, pdu: bytes) -> bytes:
        with self._lock:
            if self._sock is None:
                self.connect()
            if self._sock is None:
                raise ModbusTCPError("Not connected")

            self._transaction_id = (self._transaction_id + 1) & 0xFFFF
            tid = self._transaction_id
            unit_id = int(self._config.unit_id) & 0xFF
            length = len(pdu) + 1  # unit id + pdu
            mbap = struct.pack(">HHH", tid, 0, length) + struct.pack(">B", unit_id)

            try:
                logger.debug(
                    "Modbus request tx=%s unit_id=%s length=%s",
                    tid,
                    unit_id,
                    length,
                )
                self._sock.sendall(mbap + pdu)
                header = _recv_exact(self._sock, 7)
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
                logger.debug(
                    "Modbus response tx=%s unit_id=%s length=%s",
                    resp_tid,
                    resp_unit,
                    resp_length,
                )
                return payload
            except (OSError, socket.timeout) as exc:
                self.close()
                logger.error("Modbus TCP request failed: %s", exc)
                raise ModbusTCPError(f"Modbus TCP request failed: {exc}") from exc
