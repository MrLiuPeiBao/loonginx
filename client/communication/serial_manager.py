import logging
import threading
import time
from typing import List, Optional

import minimalmodbus
import serial


class SerialManager:
    """Thread-safe wrapper around minimalmodbus instruments."""

    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 0.5):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._instruments: dict[int, minimalmodbus.Instrument] = {}
        self._lock = threading.RLock()

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
            if serial_port.is_open:
                serial_port.close()
            instrument.close_port_after_each_call = True
            instrument.clear_buffers_before_each_transaction = True
            self._instruments[address] = instrument
            logging.info("Created Modbus instrument address=%s", address)
        return instrument

    def get_instrument(self, address: int) -> minimalmodbus.Instrument:
        with self._lock:
            return self._get_or_create_instrument(address)

    def read_registers(
        self,
        address: int,
        register: int,
        count: int,
        *,
        function_code: int = 3,
    ) -> Optional[List[int]]:
        with self._lock:
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
        retries: int = 3,
        response_delay: float = 0.02,
        timeout: Optional[float] = None,
    ) -> Optional[List[int]]:
        if count <= 0:
            logging.error("Invalid register count=%s for address=%s", count, address)
            return None

        timeout = timeout or self.timeout
        request = self._build_read_request(address, register, count, function_code)

        with self._lock:
            for attempt in range(1, retries + 1):
                frame = self._perform_direct_request(
                    address,
                    request,
                    function_code,
                    response_delay,
                    timeout,
                )
                if not frame:
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
                    logging.debug(
                        "Direct read ok address=%s register=%s data=%s",
                        address,
                        register,
                        registers,
                    )
                    return registers

            logging.error(
                "Direct Modbus read failed after %s attempts address=%s register=%s",
                retries,
                address,
                register,
            )
            return None

    def send_raw_command(self, command: bytes) -> Optional[bytes]:
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

        with self._lock:
            try:
                instrument = self._get_or_create_instrument(device_address)
                serial_port = instrument.serial
                if not serial_port.is_open:
                    serial_port.open()

                serial_port.reset_input_buffer()
                serial_port.reset_output_buffer()
                serial_port.write(command)
                serial_port.flush()

                expected_length = self._estimate_response_length(command)
                response = serial_port.read(expected_length)

                if expected_length and len(response) < expected_length:
                    remaining = expected_length - len(response)
                    if remaining > 0:
                        response += serial_port.read(remaining)

                if not response:
                    logging.error("No response from device address=%s", device_address)
                    return None

                if not self._validate_crc(response):
                    logging.error(
                        "Invalid CRC in raw response address=%s response=%s",
                        device_address,
                        response.hex(" "),
                    )
                    return None

                logging.debug(
                    "Command sent address=%s command=%s response=%s",
                    device_address,
                    command.hex(" "),
                    response.hex(" "),
                )
                return bytes(response)
            except Exception as exc:
                logging.error("Raw command failed address=%s error=%s", device_address, exc)
                return None
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
            serial_port.reset_input_buffer()
            serial_port.reset_output_buffer()
            serial_port.write(request)
            serial_port.flush()
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

    def close_all(self) -> None:
        with self._lock:
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
