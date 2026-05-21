import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable, List, Optional

import minimalmodbus
import serial

try:
    from serial.rs485 import RS485Settings
except Exception:  # pragma: no cover - depends on pyserial build/platform
    RS485Settings = None


class SerialManager:
    """Thread-safe wrapper around minimalmodbus instruments."""

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: float = 0.5,
        direct_retries: int = 3,
        direct_response_delay: float = 0.02,
        direct_timeout: Optional[float] = None,
        lock_timeout: float = 8.0,
        rs485_enabled: bool = False,
        rs485_rts_level_for_tx: bool = True,
        rs485_rts_level_for_rx: bool = False,
        rs485_loopback: bool = False,
        rs485_delay_before_tx: float = 0.0,
        rs485_delay_before_rx: float = 0.0,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.direct_retries = direct_retries
        self.direct_response_delay = direct_response_delay
        self.direct_timeout = direct_timeout
        self.lock_timeout = max(0.1, float(lock_timeout or 8.0))
        self.rs485_enabled = bool(rs485_enabled)
        self.rs485_rts_level_for_tx = bool(rs485_rts_level_for_tx)
        self.rs485_rts_level_for_rx = bool(rs485_rts_level_for_rx)
        self.rs485_loopback = bool(rs485_loopback)
        self.rs485_delay_before_tx = float(rs485_delay_before_tx or 0.0)
        self.rs485_delay_before_rx = float(rs485_delay_before_rx or 0.0)
        self._instruments: dict[int, minimalmodbus.Instrument] = {}
        self._lock = threading.RLock()
        self._rs485_log_done = False
        self._transaction_observer: Optional[Callable[..., None]] = None

    def set_transaction_observer(self, callback: Optional[Callable[..., None]]) -> None:
        self._transaction_observer = callback

    def _record_transaction(self, **payload) -> None:
        if self._transaction_observer is None:
            return
        try:
            self._transaction_observer(**payload)
        except Exception as exc:
            logging.debug("Transaction observer failed error=%s payload=%s", exc, payload)

    @contextmanager
    def _serial_lock(self, operation: str):
        acquired = self._lock.acquire(timeout=self.lock_timeout)
        if not acquired:
            logging.error(
                "Serial lock timeout operation=%s port=%s timeout=%.2fs",
                operation,
                self.port,
                self.lock_timeout,
            )
            yield False
            return
        try:
            yield True
        finally:
            self._lock.release()

    def _get_or_create_instrument(self, address: int) -> minimalmodbus.Instrument:
        instrument = self._instruments.get(address)
        if instrument is None:
            instrument = minimalmodbus.Instrument(self.port, address)
            serial_port = instrument.serial
            serial_port.baudrate = self.baudrate
            serial_port.bytesize = 8
            serial_port.parity = serial.PARITY_NONE
            serial_port.stopbits = 1
            serial_port.timeout = self.timeout
            serial_port.write_timeout = self.timeout
            serial_port.inter_byte_timeout = min(self.timeout / 2, 0.05)
            self._apply_rs485_mode(serial_port)
            if serial_port.is_open:
                serial_port.close()
            instrument.close_port_after_each_call = True
            instrument.clear_buffers_before_each_transaction = True
            self._instruments[address] = instrument
            logging.info("Created Modbus instrument address=%s", address)
        return instrument

    def _apply_rs485_mode(self, serial_port: serial.Serial) -> None:
        if not self.rs485_enabled:
            return

        if RS485Settings is None:
            logging.error("RS485 mode requested but pyserial RS485Settings is unavailable")
            return

        try:
            serial_port.rs485_mode = RS485Settings(
                rts_level_for_tx=self.rs485_rts_level_for_tx,
                rts_level_for_rx=self.rs485_rts_level_for_rx,
                loopback=self.rs485_loopback,
                delay_before_tx=self.rs485_delay_before_tx,
                delay_before_rx=self.rs485_delay_before_rx,
            )
            if not self._rs485_log_done:
                logging.info(
                    "RS485 mode enabled port=%s tx_rts=%s rx_rts=%s loopback=%s "
                    "delay_tx=%s delay_rx=%s",
                    self.port,
                    self.rs485_rts_level_for_tx,
                    self.rs485_rts_level_for_rx,
                    self.rs485_loopback,
                    self.rs485_delay_before_tx,
                    self.rs485_delay_before_rx,
                )
                self._rs485_log_done = True
        except Exception as exc:
            logging.error("Failed to apply RS485 mode port=%s error=%s", self.port, exc)

    def get_instrument(self, address: int) -> minimalmodbus.Instrument:
        with self._serial_lock("get_instrument") as acquired:
            if not acquired:
                raise TimeoutError(f"serial lock timeout port={self.port}")
            return self._get_or_create_instrument(address)

    def read_registers(
        self,
        address: int,
        register: int,
        count: int,
        *,
        function_code: int = 3,
    ) -> Optional[List[int]]:
        with self._serial_lock("read_registers") as acquired:
            if not acquired:
                return None
            try:
                instrument = self._get_or_create_instrument(address)
                data = instrument.read_registers(register, count, functioncode=function_code)
                logging.debug(
                    "Read registers ok address=%s register=%s count=%s function=%s data=%s",
                    address,
                    register,
                    count,
                    function_code,
                    data,
                )
                return data
            except Exception as exc:
                logging.error(
                    "Read registers failed address=%s register=%s function=%s error=%s",
                    address,
                    register,
                    function_code,
                    exc,
                )
                return None

    def read_registers_direct(
        self,
        address: int,
        register: int,
        count: int,
        *,
        function_code: int = 3,
        retries: Optional[int] = None,
        response_delay: Optional[float] = None,
        timeout: Optional[float] = None,
    ) -> Optional[List[int]]:
        if count <= 0:
            logging.error("Invalid register count=%s for address=%s", count, address)
            return None

        if retries is None:
            retries = self.direct_retries
        if response_delay is None:
            response_delay = self.direct_response_delay
        timeout = timeout or self.direct_timeout or self.timeout
        request = self._build_read_request(address, register, count, function_code)
        observed_retries = max(1, int(retries or 1))
        started_at = time.monotonic()
        final_status = "timeout"
        first_attempt_status: Optional[str] = None
        attempt_statuses: List[str] = []
        ok_attempt: Optional[int] = None

        with self._serial_lock("read_registers_direct") as acquired:
            if not acquired:
                self._record_transaction(
                    operation="direct_read",
                    address=address,
                    function_code=function_code,
                    register=register,
                    count=count,
                    request_length=len(request),
                    expected_length=3 + (count * 2) + 2,
                    retries=observed_retries,
                    attempts=0,
                    first_attempt_status="lock_timeout",
                    ok_attempt=None,
                    duration_ms=0.0,
                    status="lock_timeout",
                )
                return None
            for attempt in range(1, retries + 1):
                frame = self._perform_direct_request(
                    address,
                    request,
                    function_code,
                    response_delay,
                    timeout,
                )
                if not frame:
                    if first_attempt_status is None:
                        first_attempt_status = "timeout"
                    attempt_statuses.append("timeout")
                    logging.warning(
                        "Direct read attempt %s/%s failed address=%s register=%s",
                        attempt,
                        retries,
                        address,
                        register,
                    )
                    continue

                registers = self._extract_registers_from_frame(frame, count)
                if registers is not None:
                    final_status = "ok"
                    ok_attempt = attempt
                    if first_attempt_status is None:
                        first_attempt_status = "ok"
                    attempt_statuses.append("ok")
                    self._record_transaction(
                        operation="direct_read",
                        address=address,
                        function_code=function_code,
                        register=register,
                        count=count,
                        request_length=len(request),
                        expected_length=3 + (count * 2) + 2,
                        retries=observed_retries,
                        attempts=attempt,
                        first_attempt_status=first_attempt_status,
                        ok_attempt=ok_attempt,
                        attempt_statuses=tuple(attempt_statuses),
                        duration_ms=(time.monotonic() - started_at) * 1000.0,
                        status=final_status,
                    )
                    if ok_attempt and ok_attempt > 1:
                        logging.info(
                            "Direct Modbus read recovered address=%s register=%s ok_attempt=%s/%s",
                            address,
                            register,
                            ok_attempt,
                            observed_retries,
                        )
                    logging.debug(
                        "Direct read ok address=%s register=%s data=%s",
                        address,
                        register,
                        registers,
                    )
                    return registers
                if first_attempt_status is None:
                    first_attempt_status = "parse_error"
                attempt_statuses.append("parse_error")

            logging.error(
                "Direct Modbus read failed after %s attempts address=%s register=%s",
                retries,
                address,
                register,
            )
            self._record_transaction(
                operation="direct_read",
                address=address,
                function_code=function_code,
                register=register,
                count=count,
                request_length=len(request),
                expected_length=3 + (count * 2) + 2,
                retries=observed_retries,
                attempts=observed_retries,
                first_attempt_status=first_attempt_status or final_status,
                ok_attempt=ok_attempt,
                attempt_statuses=tuple(attempt_statuses),
                duration_ms=(time.monotonic() - started_at) * 1000.0,
                status=final_status,
            )
            return None

    def send_raw_command(self, command: bytes) -> Optional[bytes]:
        return self.send_raw_command_with_options(command)

    def send_raw_command_with_options(
        self,
        command: bytes,
        *,
        wait_for_tx_complete: bool = True,
        response_timeout: Optional[float] = None,
    ) -> Optional[bytes]:
        if not command:
            logging.error("Empty command payload")
            return None

        if not isinstance(command, (bytes, bytearray, memoryview)):
            try:
                command = bytes(command)
            except Exception as exc:
                logging.error("Unable to coerce command to bytes: %s", exc)
                return None
        else:
            command = bytes(command)

        device_address = command[0]
        started_at = time.monotonic()
        expected_length = self._estimate_response_length(command)

        with self._serial_lock("send_raw_command") as acquired:
            if not acquired:
                self._record_transaction(
                    operation="raw_command",
                    address=device_address,
                    function_code=command[1] if len(command) > 1 else None,
                    register=None,
                    count=None,
                    request_length=len(command),
                    expected_length=expected_length,
                    retries=1,
                    duration_ms=0.0,
                    status="lock_timeout",
                )
                return None
            original_timeout = None
            serial_port = None
            try:
                instrument = self._get_or_create_instrument(device_address)
                serial_port = instrument.serial
                if not serial_port.is_open:
                    serial_port.open()
                self._apply_rs485_mode(serial_port)
                original_timeout = serial_port.timeout
                if response_timeout is not None:
                    serial_port.timeout = max(float(response_timeout), 0.01)

                serial_port.reset_input_buffer()
                serial_port.reset_output_buffer()
                serial_port.write(command)
                if wait_for_tx_complete:
                    self._wait_for_tx_complete(serial_port, len(command))

                response = serial_port.read(expected_length)

                if expected_length and len(response) < expected_length:
                    remaining = expected_length - len(response)
                    if remaining > 0:
                        response += serial_port.read(remaining)

                if not response:
                    logging.error("No response from device address=%s", device_address)
                    self._record_transaction(
                        operation="raw_command",
                        address=device_address,
                        function_code=command[1] if len(command) > 1 else None,
                        register=None,
                        count=None,
                        request_length=len(command),
                        expected_length=expected_length,
                        retries=1,
                        duration_ms=(time.monotonic() - started_at) * 1000.0,
                        status="timeout",
                    )
                    return None

                if not self._validate_crc(response):
                    logging.error(
                        "Invalid CRC in raw response address=%s response=%s",
                        device_address,
                        response.hex(" "),
                    )
                    self._record_transaction(
                        operation="raw_command",
                        address=device_address,
                        function_code=command[1] if len(command) > 1 else None,
                        register=None,
                        count=None,
                        request_length=len(command),
                        expected_length=expected_length,
                        retries=1,
                        duration_ms=(time.monotonic() - started_at) * 1000.0,
                        status="crc_error",
                    )
                    return None

                self._record_transaction(
                    operation="raw_command",
                    address=device_address,
                    function_code=command[1] if len(command) > 1 else None,
                    register=None,
                    count=None,
                    request_length=len(command),
                    expected_length=expected_length,
                    retries=1,
                    duration_ms=(time.monotonic() - started_at) * 1000.0,
                    status="ok",
                )
                logging.debug(
                    "Command sent address=%s command=%s response=%s",
                    device_address,
                    command.hex(" "),
                    response.hex(" "),
                )
                return bytes(response)
            except Exception as exc:
                logging.error("Raw command failed address=%s error=%s", device_address, exc)
                self._record_transaction(
                    operation="raw_command",
                    address=device_address,
                    function_code=command[1] if len(command) > 1 else None,
                    register=None,
                    count=None,
                    request_length=len(command),
                    expected_length=expected_length,
                    retries=1,
                    duration_ms=(time.monotonic() - started_at) * 1000.0,
                    status="exception",
                )
                return None
            finally:
                if serial_port is not None and original_timeout is not None:
                    try:
                        serial_port.timeout = original_timeout
                    except Exception:
                        pass
                if serial_port is not None:
                    try:
                        if serial_port.is_open:
                            serial_port.close()
                    except Exception:
                        pass

    def _perform_direct_request(
        self,
        address: int,
        request: bytes,
        function_code: int,
        response_delay: float,
        timeout: float,
    ) -> Optional[bytes]:
        instrument = self._get_or_create_instrument(address)
        serial_port = instrument.serial
        original_timeout = serial_port.timeout
        try:
            if serial_port.is_open:
                serial_port.close()
            serial_port.timeout = max(timeout, self.timeout)
            serial_port.open()
            self._apply_rs485_mode(serial_port)
            serial_port.reset_input_buffer()
            serial_port.reset_output_buffer()
            serial_port.write(request)
            self._wait_for_tx_complete(serial_port, len(request))
            if response_delay > 0:
                time.sleep(response_delay)
            frame = self._read_register_response(serial_port, address, function_code, timeout)
            if frame is None:
                self._drain_serial(serial_port)
            return frame
        except Exception as exc:
            logging.error(
                "Direct Modbus transaction failed address=%s function=%s error=%s",
                address,
                function_code,
                exc,
            )
            self._drain_serial(serial_port)
            return None
        finally:
            try:
                serial_port.timeout = original_timeout
            except Exception:
                pass
            try:
                if serial_port.is_open:
                    serial_port.close()
            except Exception:
                pass

    def _read_register_response(
        self,
        serial_port: serial.Serial,
        expected_address: int,
        function_code: int,
        timeout: float,
    ) -> Optional[bytes]:
        deadline = time.monotonic() + max(timeout, 0.05)
        header = self._read_exact(serial_port, 2, deadline)
        if len(header) < 2:
            logging.debug(
                "Response header timeout address=%s function=%s",
                expected_address,
                function_code,
            )
            return None

        address, response_function = header
        if address != expected_address:
            logging.error(
                "Unexpected slave address response=%s expected=%s function=%s",
                address,
                expected_address,
                function_code,
            )
            return None

        if response_function & 0x80:
            exception_code = serial_port.read(1)
            crc_bytes = serial_port.read(2)
            logging.error(
                "Modbus exception code=%s address=%s function=%s",
                exception_code.hex() if exception_code else "??",
                expected_address,
                function_code,
            )
            return None

        if response_function != function_code:
            logging.error(
                "Unexpected function code response=%s expected=%s address=%s",
                response_function,
                function_code,
                expected_address,
            )
            return None

        byte_count_bytes = self._read_exact(serial_port, 1, deadline)
        if len(byte_count_bytes) < 1:
            logging.error(
                "Missing byte count address=%s function=%s",
                expected_address,
                function_code,
            )
            return None

        byte_count = byte_count_bytes[0]
        data = self._read_exact(serial_port, byte_count, deadline)
        if len(data) < byte_count:
            logging.error(
                "Incomplete data payload bytes=%s expected=%s address=%s function=%s",
                len(data),
                byte_count,
                expected_address,
                function_code,
            )
            return None

        crc = self._read_exact(serial_port, 2, deadline)
        if len(crc) < 2:
            logging.error(
                "Missing CRC address=%s function=%s",
                expected_address,
                function_code,
            )
            return None

        frame = header + byte_count_bytes + data + crc
        if not self._validate_crc(frame):
            logging.error(
                "Invalid CRC in direct response address=%s function=%s frame=%s",
                expected_address,
                function_code,
                frame.hex(" "),
            )
            return None

        return frame

    @staticmethod
    def _extract_registers_from_frame(frame: bytes, expected_count: int) -> Optional[List[int]]:
        if len(frame) < 5:
            return None

        byte_count = frame[2]
        data = frame[3:-2]
        if byte_count != len(data):
            logging.error(
                "Byte count mismatch byte_count=%s data_bytes=%s",
                byte_count,
                len(data),
            )
            return None

        if byte_count % 2 != 0:
            logging.error("Byte count %s is not even", byte_count)
            return None

        registers: List[int] = []
        for idx in range(0, byte_count, 2):
            registers.append(int.from_bytes(data[idx : idx + 2], byteorder="big", signed=False))

        if len(registers) < expected_count:
            logging.error(
                "Received %s registers but expected %s",
                len(registers),
                expected_count,
            )
            return None

        return registers[:expected_count]

    @staticmethod
    def _estimate_response_length(command: bytes) -> int:
        if len(command) < 2:
            return 0

        function_code = command[1]

        if function_code in (0x01, 0x02, 0x03, 0x04) and len(command) >= 6:
            quantity = (command[4] << 8) | command[5]
            if function_code in (0x01, 0x02):
                byte_count = (quantity + 7) // 8
            else:
                byte_count = quantity * 2
            return 3 + byte_count + 2  # addr + func + byte count + data + CRC

        return 8

    def _wait_for_tx_complete(self, serial_port: serial.Serial, payload_length: int) -> None:
        estimated = self._estimate_tx_airtime_seconds(serial_port, payload_length)
        deadline = time.monotonic() + min(max(estimated + 0.05, 0.05), 0.5)

        if estimated > 0:
            time.sleep(min(estimated, 0.1))

        while time.monotonic() < deadline:
            try:
                out_waiting = int(getattr(serial_port, "out_waiting", 0) or 0)
            except Exception:
                out_waiting = 0
            if out_waiting <= 0:
                return
            time.sleep(0.005)

        logging.warning(
            "TX drain wait timed out port=%s payload_length=%s baudrate=%s",
            self.port,
            payload_length,
            self.baudrate,
        )

    @staticmethod
    def _estimate_tx_airtime_seconds(serial_port: serial.Serial, payload_length: int) -> float:
        if payload_length <= 0:
            return 0.0
        try:
            baudrate = float(getattr(serial_port, "baudrate", 0) or 0)
            if baudrate <= 0:
                return 0.0
            bytesize = int(getattr(serial_port, "bytesize", 8) or 8)
            parity = str(getattr(serial_port, "parity", "N") or "N").upper()
            stopbits = float(getattr(serial_port, "stopbits", 1) or 1)
        except Exception:
            return 0.0

        parity_bits = 0 if parity in {"N", "NONE"} else 1
        bits_per_char = 1.0 + max(bytesize, 5) + parity_bits + max(stopbits, 1.0)
        return (payload_length * bits_per_char) / baudrate

    def close_all(self) -> None:
        with self._serial_lock("close_all") as acquired:
            if not acquired:
                logging.error("Skip closing serial instruments because lock is stuck port=%s", self.port)
                return
            for address, instrument in self._instruments.items():
                try:
                    serial_port = instrument.serial
                    if serial_port.is_open:
                        serial_port.close()
                except Exception as exc:
                    logging.error("Failed to close instrument address=%s error=%s", address, exc)
            self._instruments.clear()

    @staticmethod
    def _validate_crc(frame: bytes) -> bool:
        if len(frame) < 5:
            return False
        payload = frame[:-2]
        received_crc = int.from_bytes(frame[-2:], byteorder="little")
        return received_crc == SerialManager._calculate_crc(payload)

    @staticmethod
    def _calculate_crc(payload: bytes) -> int:
        crc = 0xFFFF
        for byte in payload:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc & 0xFFFF

    @staticmethod
    def _build_read_request(address: int, register: int, count: int, function_code: int) -> bytes:
        payload = bytearray()
        payload.append(address & 0xFF)
        payload.append(function_code & 0xFF)
        payload.extend(register.to_bytes(2, byteorder="big", signed=False))
        payload.extend(count.to_bytes(2, byteorder="big", signed=False))
        crc = SerialManager._calculate_crc(payload)
        payload.extend(crc.to_bytes(2, byteorder="little"))
        return bytes(payload)

    @staticmethod
    def _read_exact(serial_port: serial.Serial, size: int, deadline: float) -> bytes:
        buffer = bytearray()
        while len(buffer) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            serial_port.timeout = max(min(remaining, 1.0), 0.01)
            chunk = serial_port.read(size - len(buffer))
            if chunk:
                buffer.extend(chunk)
            else:
                continue
        return bytes(buffer)

    @staticmethod
    def _drain_serial(serial_port: serial.Serial) -> None:
        try:
            if serial_port.in_waiting:
                serial_port.read(serial_port.in_waiting)
        except Exception:
            pass
        try:
            serial_port.reset_input_buffer()
        except Exception:
            pass
