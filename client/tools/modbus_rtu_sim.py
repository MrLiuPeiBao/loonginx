#!/usr/bin/env python3
import argparse
import json
import os
import pty
import select
import struct
import sys
import time
import tty
from typing import Dict, Optional, Tuple


class RangeGenerator:
    def __init__(self, min_value: float, max_value: float, step: float):
        self.min_value = float(min_value)
        self.max_value = float(max_value)
        self.step = float(step)
        self.current = float(min_value)

    def next(self) -> float:
        value = self.current
        self.current += self.step
        if self.step >= 0 and self.current > self.max_value:
            self.current = self.min_value
        elif self.step < 0 and self.current < self.max_value:
            self.current = self.min_value
        return value


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def add_crc(payload: bytes) -> bytes:
    crc = crc16_modbus(payload)
    return payload + crc.to_bytes(2, byteorder="little")


def parse_request(frame: bytes) -> Optional[Tuple[int, int, int, int]]:
    if len(frame) != 8:
        return None
    body = frame[:-2]
    recv_crc = int.from_bytes(frame[-2:], byteorder="little")
    if crc16_modbus(body) != recv_crc:
        return None
    address = frame[0]
    function = frame[1]
    register = (frame[2] << 8) | frame[3]
    count = (frame[4] << 8) | frame[5]
    return address, function, register, count


def encode_dcba(value: float) -> Tuple[int, int]:
    packed = struct.pack(">f", float(value))
    dcba = bytes(reversed(packed))
    return (
        int.from_bytes(dcba[0:2], byteorder="big"),
        int.from_bytes(dcba[2:4], byteorder="big"),
    )


def encode_scaled(value: float, multiplier: float, offset: float) -> int:
    if multiplier == 0:
        return 0
    raw = int(round(value / multiplier - offset))
    if raw < 0:
        return 0
    if raw > 0xFFFF:
        return 0xFFFF
    return raw


def load_overrides(path: str) -> Dict[str, Dict[str, float]]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


class ModbusSimulator:
    def __init__(self, overrides: Dict[str, Dict[str, float]]):
        self.sensors = {
            "temperature": {
                "address": 30,
                "register": 16,
                "count": 2,
                "format": "dcba",
                "min": 0.0,
                "max": 40.0,
                "step": 0.1,
            },
            "humidity": {
                "address": 31,
                "register": 16,
                "count": 2,
                "format": "dcba",
                "min": 30.0,
                "max": 80.0,
                "step": 0.5,
            },
            "pressure": {
                "address": 32,
                "register": 16,
                "count": 2,
                "format": "dcba",
                "min": 90.0,
                "max": 110.0,
                "step": 0.1,
            },
            "co": {
                "address": 1,
                "register": 0x65,
                "count": 1,
                "format": "raw",
                "min": 0.0,
                "max": 100.0,
                "step": 1.0,
            },
            "h2s": {
                "address": 2,
                "register": 0x65,
                "count": 1,
                "format": "raw",
                "min": 0.0,
                "max": 50.0,
                "step": 1.0,
            },
            "o2": {
                "address": 3,
                "register": 0x65,
                "count": 1,
                "format": "o2",
                "min": 19.0,
                "max": 21.0,
                "step": 0.1,
            },
            "ch4": {
                "address": 4,
                "register": 0x65,
                "count": 1,
                "format": "raw",
                "min": 0.0,
                "max": 100.0,
                "step": 1.0,
            },
            "smoke": {
                "address": 5,
                "register": 0x00,
                "count": 1,
                "format": "smoke",
                "min": 0.0,
                "max": 10.0,
                "step": 0.1,
            },
        }

        self.bms = {
            "address": 210,
            "voltage": {
                "addr": 0x28,
                "min": 11.0,
                "max": 13.0,
                "step": 0.05,
                "multiplier": 0.1,
                "offset": 0.0,
            },
            "soc": {
                "addr": 0x2A,
                "min": 20.0,
                "max": 100.0,
                "step": 1.0,
                "multiplier": 0.001,
                "offset": 0.0,
            },
            "status": {
                "addr": 0x2F,
                "min": 0.0,
                "max": 5.0,
                "step": 1.0,
                "multiplier": 1.0,
                "offset": 0.0,
            },
            "capacity": {
                "addr": 0x30,
                "min": 10.0,
                "max": 30.0,
                "step": 0.1,
                "multiplier": 0.1,
                "offset": 0.0,
            },
            "power": {
                "addr": 0x39,
                "min": 0.0,
                "max": 200.0,
                "step": 1.0,
                "multiplier": 1.0,
                "offset": 0.0,
            },
            "current": {
                "addr": 0x29,
                "min": -5.0,
                "max": 5.0,
                "step": 0.1,
                "multiplier": 0.1,
                "offset": -30000.0,
            },
            "cell_voltages": {
                "addr": 0x00,
                "count": 8,
                "min": 3.2,
                "max": 3.4,
                "step": 0.001,
                "multiplier": 0.001,
                "offset": 0.0,
                "delta": 0.001,
            },
        }

        self._apply_overrides(overrides)
        self._generators = {}
        for name, cfg in self.sensors.items():
            self._generators[name] = RangeGenerator(cfg["min"], cfg["max"], cfg["step"])
        for name, cfg in self.bms.items():
            if name == "address":
                continue
            self._generators[f"bms_{name}"] = RangeGenerator(cfg["min"], cfg["max"], cfg["step"])

        self._sensor_by_address = {cfg["address"]: (name, cfg) for name, cfg in self.sensors.items()}

    def _apply_overrides(self, overrides: Dict[str, Dict[str, float]]) -> None:
        if not overrides:
            return
        for name, override in overrides.items():
            if name in self.sensors:
                self._apply_range(self.sensors[name], override)
            if name == "bms" and isinstance(override, dict):
                for key, sub in override.items():
                    if key in self.bms and isinstance(sub, dict):
                        self._apply_range(self.bms[key], sub)

    @staticmethod
    def _apply_range(target: Dict[str, float], override: Dict[str, float]) -> None:
        for key in ("min", "max", "step", "delta"):
            if key in override:
                try:
                    target[key] = float(override[key])
                except Exception:
                    continue

    def handle_request(self, address: int, function: int, register: int, count: int) -> Optional[bytes]:
        if function not in (3, 4):
            return self._exception(address, function, 0x01)

        sensor_entry = self._sensor_by_address.get(address)
        if sensor_entry:
            name, cfg = sensor_entry
            if register != cfg["register"] or count != cfg["count"]:
                return self._exception(address, function, 0x02)
            value = self._generators[name].next()
            if cfg["format"] == "dcba":
                registers = encode_dcba(value)
            elif cfg["format"] == "o2":
                registers = (int(round(value * 10)),)
            elif cfg["format"] == "smoke":
                registers = (int(round(value * 10)),)
            else:
                registers = (int(round(value)),)
            return self._build_response(address, function, registers)

        if address == self.bms["address"]:
            return self._handle_bms(address, function, register, count)

        return self._exception(address, function, 0x02)

    def _handle_bms(self, address: int, function: int, register: int, count: int) -> Optional[bytes]:
        registers = []
        cell_cfg = self.bms["cell_voltages"]
        cell_start = cell_cfg["addr"]
        cell_end = cell_start + cell_cfg["count"] - 1
        cell_values = None

        for offset in range(count):
            reg = register + offset
            if cell_start <= reg <= cell_end:
                if cell_values is None:
                    base = self._generators["bms_cell_voltages"].next()
                    cell_values = []
                    for idx in range(cell_cfg["count"]):
                        value = base + idx * cell_cfg.get("delta", 0.0)
                        if value > cell_cfg["max"]:
                            value = cell_cfg["min"]
                        raw = encode_scaled(value, cell_cfg["multiplier"], cell_cfg["offset"])
                        cell_values.append(raw)
                registers.append(cell_values[reg - cell_start])
                continue

            mapped = None
            for key, cfg in self.bms.items():
                if key in ("address", "cell_voltages"):
                    continue
                if reg == cfg["addr"]:
                    value = self._generators[f"bms_{key}"].next()
                    mapped = encode_scaled(value, cfg["multiplier"], cfg["offset"])
                    break

            if mapped is None:
                return self._exception(address, function, 0x02)
            registers.append(mapped)

        return self._build_response(address, function, tuple(registers))

    @staticmethod
    def _build_response(address: int, function: int, registers: Tuple[int, ...]) -> bytes:
        data = b"".join(int(val).to_bytes(2, byteorder="big", signed=False) for val in registers)
        payload = bytes([address, function, len(data)]) + data
        return add_crc(payload)

    @staticmethod
    def _exception(address: int, function: int, code: int) -> bytes:
        payload = bytes([address, function | 0x80, code])
        return add_crc(payload)


def run(link_path: str, config_path: str) -> None:
    overrides = load_overrides(config_path)
    simulator = ModbusSimulator(overrides)

    master_fd, slave_fd = pty.openpty()
    tty.setraw(slave_fd)
    slave_name = os.ttyname(slave_fd)

    try:
        if os.path.islink(link_path) or os.path.exists(link_path):
            os.unlink(link_path)
        os.symlink(slave_name, link_path)
    except OSError as exc:
        print(f"Failed to create symlink {link_path} -> {slave_name}: {exc}")
        sys.exit(1)

    print(f"Modbus RTU simulator listening on {slave_name} -> {link_path}")
    print("Press Ctrl+C to stop.")

    buffer = bytearray()
    try:
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.5)
            if master_fd in ready:
                chunk = os.read(master_fd, 256)
                if not chunk:
                    continue
                buffer.extend(chunk)

            while len(buffer) >= 8:
                frame = bytes(buffer[:8])
                parsed = parse_request(frame)
                if not parsed:
                    buffer.pop(0)
                    continue
                address, function, register, count = parsed
                response = simulator.handle_request(address, function, register, count)
                if response:
                    os.write(master_fd, response)
                del buffer[:8]
    except KeyboardInterrupt:
        print("Stopping simulator...")
    finally:
        try:
            if os.path.islink(link_path):
                os.unlink(link_path)
        except OSError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Modbus RTU sensor simulator")
    parser.add_argument("--link", default="/tmp/ttyS_sim", help="symbolic link path for slave TTY")
    parser.add_argument(
        "--config",
        default="/client/modbus_sim_config.json",
        help="override ranges json path",
    )
    args = parser.parse_args()
    run(args.link, args.config)


if __name__ == "__main__":
    main()
