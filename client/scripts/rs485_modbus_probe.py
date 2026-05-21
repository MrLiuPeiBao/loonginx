#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import serial

try:
    from serial.rs485 import RS485Settings
except Exception:  # pragma: no cover - platform dependent
    RS485Settings = None


@dataclass(frozen=True)
class Target:
    name: str
    address: int
    function: int
    register: int
    count: int
    timeout_s: float
    post_tx_delay_s: float
    raw_hex: str = ""
    group: str = ""


TARGETS: Dict[str, Target] = {
    "env-temp": Target("env-temp", 15, 3, 0x0001, 1, 1.8, 0.08, group="env"),
    "env-humi": Target("env-humi", 15, 3, 0x0002, 1, 1.8, 0.08, group="env"),
    "env-smoke": Target("env-smoke", 15, 3, 0x000B, 1, 1.8, 0.08, group="env"),
    "co": Target("co", 1, 3, 0x0065, 2, 1.2, 0.08, group="gas"),
    "h2s": Target("h2s", 2, 3, 0x0065, 2, 1.2, 0.08, group="gas"),
    "o2": Target("o2", 3, 3, 0x0065, 2, 1.2, 0.08, group="gas"),
    "ch4": Target("ch4", 4, 3, 0x0065, 2, 1.2, 0.08, group="gas"),
    "photo": Target("photo", 25, 2, 0x0000, 2, 0.25, 0.0, group="photo"),
    "bms-voltage": Target("bms-voltage", 210, 3, 0x0028, 1, 1.0, 0.05, group="bms-fast"),
    "bms-current": Target("bms-current", 210, 3, 0x0029, 1, 1.0, 0.05, group="bms-fast"),
    "bms-soc": Target("bms-soc", 210, 3, 0x002A, 1, 1.0, 0.05, group="bms-fast"),
    "bms-status": Target("bms-status", 210, 3, 0x002F, 1, 1.0, 0.05, group="bms-fast"),
    "bms-capacity": Target("bms-capacity", 210, 3, 0x0030, 1, 1.0, 0.05, group="bms-slow"),
    "bms-power": Target("bms-power", 210, 3, 0x0039, 1, 1.0, 0.05, group="bms-slow"),
    "bms-cells": Target("bms-cells", 210, 3, 0x0000, 8, 1.0, 0.05, group="bms-slow"),
}

SCENARIOS: Dict[str, Sequence[str]] = {
    "env": ("env-temp", "env-humi", "env-smoke"),
    "gas": ("co", "h2s", "o2", "ch4"),
    "bms-fast": ("bms-voltage", "bms-current", "bms-soc", "bms-status"),
    "bms-slow": ("bms-capacity", "bms-power", "bms-cells"),
    "production-lite": (
        "photo",
        "env-temp",
        "env-humi",
        "env-smoke",
        "co",
        "h2s",
        "o2",
        "ch4",
        "bms-voltage",
        "bms-current",
        "bms-soc",
        "bms-status",
    ),
    "all": tuple(TARGETS.keys()),
}


def json_line(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def crc16_modbus(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc(payload: bytes) -> bytes:
    crc = crc16_modbus(payload)
    return payload + crc.to_bytes(2, "little")


def build_request(target: Target) -> bytes:
    if target.raw_hex:
        return bytes.fromhex(target.raw_hex)
    payload = bytes(
        [
            target.address & 0xFF,
            target.function & 0xFF,
            (target.register >> 8) & 0xFF,
            target.register & 0xFF,
            (target.count >> 8) & 0xFF,
            target.count & 0xFF,
        ]
    )
    return append_crc(payload)


def expected_response_length(target: Target) -> int:
    if target.function in (1, 2):
        byte_count = (target.count + 7) // 8
    elif target.function in (3, 4):
        byte_count = target.count * 2
    else:
        return 8
    return 3 + byte_count + 2


def classify_response(target: Target, frame: bytes, expected_len: int) -> str:
    if not frame:
        return "timeout"
    if len(frame) < 2:
        return "short_frame"
    if frame[0] != target.address:
        return "unexpected_address"
    if frame[1] & 0x80:
        return "modbus_exception"
    if frame[1] != target.function:
        return "unexpected_function"
    if len(frame) < expected_len:
        return "short_frame"
    if len(frame) >= 5 and crc16_modbus(frame[:-2]) != int.from_bytes(frame[-2:], "little"):
        return "crc_error"
    return "ok"


def read_until_deadline(port: serial.Serial, expected_len: int, timeout_s: float) -> bytes:
    deadline = time.monotonic() + max(timeout_s, 0.01)
    chunks = bytearray()
    while len(chunks) < expected_len:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        port.timeout = max(min(remaining, 0.2), 0.01)
        data = port.read(expected_len - len(chunks))
        if data:
            chunks.extend(data)
    try:
        waiting = int(port.in_waiting or 0)
        if waiting > 0:
            chunks.extend(port.read(waiting))
    except Exception:
        pass
    return bytes(chunks)


def configure_port(args: argparse.Namespace) -> serial.Serial:
    port = serial.Serial(
        port=args.port,
        baudrate=args.baud,
        bytesize=args.bytesize,
        parity=args.parity,
        stopbits=args.stopbits,
        timeout=max(args.timeout_ms / 1000.0, 0.01),
        write_timeout=max(args.write_timeout_ms / 1000.0, 0.01),
    )
    if args.rs485:
        if RS485Settings is None:
            raise RuntimeError("serial.rs485.RS485Settings unavailable")
        port.rs485_mode = RS485Settings(
            rts_level_for_tx=args.rs485_rts_tx,
            rts_level_for_rx=args.rs485_rts_rx,
            loopback=args.rs485_loopback,
            delay_before_tx=args.rs485_delay_before_tx_ms / 1000.0,
            delay_before_rx=args.rs485_delay_before_rx_ms / 1000.0,
        )
    return port


def raw_attempt(args: argparse.Namespace, target: Target, port: Optional[serial.Serial]) -> dict:
    request = build_request(target)
    expected_len = expected_response_length(target)
    started = time.monotonic()
    owns_port = port is None
    rx = b""
    status = "exception"
    error = None
    try:
        if owns_port:
            port = configure_port(args)
        assert port is not None
        port.reset_input_buffer()
        port.reset_output_buffer()
        wrote = port.write(request)
        port.flush()
        if args.wait_tx:
            try:
                port.flush()
            except Exception:
                pass
        delay_s = args.post_tx_delay_ms / 1000.0 if args.post_tx_delay_ms is not None else target.post_tx_delay_s
        if delay_s > 0:
            time.sleep(delay_s)
        timeout_s = args.target_timeout_ms / 1000.0 if args.target_timeout_ms is not None else target.timeout_s
        rx = read_until_deadline(port, expected_len, timeout_s)
        status = classify_response(target, rx, expected_len)
        return {
            "status": status,
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "request_hex": request.hex(" "),
            "rx_hex": rx.hex(" "),
            "rx_len": len(rx),
            "expected_len": expected_len,
            "wrote": wrote,
        }
    except Exception as exc:
        error = repr(exc)
        return {
            "status": status,
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "request_hex": request.hex(" "),
            "rx_hex": rx.hex(" "),
            "rx_len": len(rx),
            "expected_len": expected_len,
            "error": error,
        }
    finally:
        if owns_port and port is not None:
            try:
                port.close()
            except Exception:
                pass


def raw_transaction(args: argparse.Namespace, target: Target, port: Optional[serial.Serial]) -> dict:
    attempts = []
    max_attempts = max(1, int(args.extra_retry) + 1)
    for attempt in range(1, max_attempts + 1):
        result = raw_attempt(args, target, port)
        attempts.append(result)
        if result["status"] == "ok":
            break
        if attempt < max_attempts and args.retry_gap_ms > 0:
            time.sleep(args.retry_gap_ms / 1000.0)
    final = attempts[-1]
    ok_attempt = next((idx + 1 for idx, item in enumerate(attempts) if item["status"] == "ok"), None)
    return {
        **final,
        "status": "ok" if ok_attempt is not None else final["status"],
        "attempts": len(attempts),
        "first_attempt_status": attempts[0]["status"],
        "attempt_statuses": [item["status"] for item in attempts],
        "ok_attempt": ok_attempt,
    }


def minimalmodbus_transaction(args: argparse.Namespace, target: Target) -> dict:
    started = time.monotonic()
    request = build_request(target)
    try:
        import minimalmodbus

        instrument = minimalmodbus.Instrument(args.port, target.address)
        instrument.serial.baudrate = args.baud
        instrument.serial.bytesize = args.bytesize
        instrument.serial.parity = args.parity
        instrument.serial.stopbits = args.stopbits
        instrument.serial.timeout = args.target_timeout_ms / 1000.0 if args.target_timeout_ms is not None else target.timeout_s
        instrument.serial.write_timeout = args.write_timeout_ms / 1000.0
        if args.rs485:
            if RS485Settings is None:
                raise RuntimeError("serial.rs485.RS485Settings unavailable")
            instrument.serial.rs485_mode = RS485Settings(
                rts_level_for_tx=args.rs485_rts_tx,
                rts_level_for_rx=args.rs485_rts_rx,
                loopback=args.rs485_loopback,
                delay_before_tx=args.rs485_delay_before_tx_ms / 1000.0,
                delay_before_rx=args.rs485_delay_before_rx_ms / 1000.0,
            )
        instrument.clear_buffers_before_each_transaction = True
        instrument.close_port_after_each_call = True
        if target.function in (3, 4):
            data = instrument.read_registers(target.register, target.count, functioncode=target.function)
        elif target.function in (1, 2):
            data = instrument.read_bits(target.register, target.count, functioncode=target.function)
        else:
            raise ValueError(f"unsupported function for minimalmodbus: {target.function}")
        return {
            "status": "ok",
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "request_hex": request.hex(" "),
            "rx_hex": "",
            "rx_len": None,
            "expected_len": expected_response_length(target),
            "data": data,
            "attempts": 1,
            "first_attempt_status": "ok",
            "attempt_statuses": ["ok"],
            "ok_attempt": 1,
        }
    except Exception as exc:
        name = exc.__class__.__name__.lower()
        status = "timeout" if "timeout" in name or "noresponse" in name else "exception"
        return {
            "status": status,
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "request_hex": request.hex(" "),
            "rx_hex": "",
            "rx_len": None,
            "expected_len": expected_response_length(target),
            "error": repr(exc),
            "attempts": 1,
            "first_attempt_status": status,
            "attempt_statuses": [status],
            "ok_attempt": None,
        }


def iter_target_names(args: argparse.Namespace) -> List[str]:
    names: List[str] = []
    if args.scenario:
        names.extend(SCENARIOS[args.scenario])
    if args.targets:
        for name in args.targets.split(","):
            stripped = name.strip()
            if stripped:
                names.append(stripped)
    if not names:
        names.append(args.target)
    unknown = [name for name in names if name not in TARGETS]
    if unknown:
        raise SystemExit(f"unknown target(s): {', '.join(unknown)}")
    return names


def emit_meta(args: argparse.Namespace, target_names: Sequence[str]) -> None:
    minimalmodbus_version = None
    if args.tool == "minimal":
        try:
            import minimalmodbus

            minimalmodbus_version = minimalmodbus.__version__
        except Exception as exc:
            minimalmodbus_version = f"unavailable:{exc!r}"
    json_line(
        {
            "type": "run_meta",
            "tool": args.tool,
            "mode": args.mode,
            "scenario": args.scenario,
            "targets": list(target_names),
            "port": args.port,
            "baud": args.baud,
            "bytesize": args.bytesize,
            "parity": args.parity,
            "stopbits": args.stopbits,
            "gap_ms": args.gap_ms,
            "extra_retry": args.extra_retry,
            "retry_gap_ms": args.retry_gap_ms,
            "open_mode": args.open_mode,
            "rs485": args.rs485,
            "rs485_rts_tx": args.rs485_rts_tx,
            "rs485_rts_rx": args.rs485_rts_rx,
            "rs485_delay_before_tx_ms": args.rs485_delay_before_tx_ms,
            "rs485_delay_before_rx_ms": args.rs485_delay_before_rx_ms,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "pyserial": getattr(serial, "VERSION", None),
            "minimalmodbus": minimalmodbus_version,
            "pid": os.getpid(),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )


def run_probe(args: argparse.Namespace) -> int:
    target_names = iter_target_names(args)
    emit_meta(args, target_names)
    sample_results: List[dict] = []
    reusable_port: Optional[serial.Serial] = None
    try:
        if args.tool == "pyraw" and args.open_mode == "keep-open":
            reusable_port = configure_port(args)

        total_cycles = args.samples if args.mode == "single" else args.cycles
        seq = 0
        for cycle in range(1, total_cycles + 1):
            for target_name in target_names:
                target = TARGETS[target_name]
                seq += 1
                if args.tool == "minimal":
                    result = minimalmodbus_transaction(args, target)
                else:
                    result = raw_transaction(args, target, reusable_port)
                payload = {
                    "type": "sample",
                    "seq": seq,
                    "cycle": cycle,
                    "target": target.name,
                    "address": target.address,
                    "function": target.function,
                    "register": target.register,
                    "count": target.count,
                    **result,
                }
                sample_results.append(payload)
                json_line(payload)
                if args.gap_ms > 0:
                    time.sleep(args.gap_ms / 1000.0)
    finally:
        if reusable_port is not None:
            try:
                reusable_port.close()
            except Exception:
                pass

    emit_summary(sample_results)
    return 0


def emit_summary(samples: Sequence[dict]) -> None:
    overall = summarize(samples)
    by_target = {}
    for target in sorted({item["target"] for item in samples}):
        by_target[target] = summarize([item for item in samples if item["target"] == target])
    json_line({"type": "summary", **overall, "by_target": by_target})


def summarize(samples: Sequence[dict]) -> dict:
    statuses: Dict[str, int] = {}
    first_statuses: Dict[str, int] = {}
    elapsed_ok = []
    for item in samples:
        statuses[item["status"]] = statuses.get(item["status"], 0) + 1
        first = item.get("first_attempt_status") or item["status"]
        first_statuses[first] = first_statuses.get(first, 0) + 1
        if item["status"] == "ok":
            elapsed_ok.append(float(item["elapsed_ms"]))
    total = len(samples)
    ok = statuses.get("ok", 0)
    first_ok = first_statuses.get("ok", 0)
    summary = {
        "samples": total,
        "ok": ok,
        "success_rate": round(ok / total, 6) if total else 0.0,
        "first_attempt_ok": first_ok,
        "first_attempt_success_rate": round(first_ok / total, 6) if total else 0.0,
        "statuses": statuses,
        "first_attempt_statuses": first_statuses,
    }
    if elapsed_ok:
        summary["elapsed_ms_p50_ok"] = round(statistics.median(elapsed_ok), 3)
        if len(elapsed_ok) >= 2:
            summary["elapsed_ms_p95_ok"] = round(statistics.quantiles(elapsed_ok, n=20)[18], 3)
        else:
            summary["elapsed_ms_p95_ok"] = round(elapsed_ok[0], 3)
    return summary


def run_listen(args: argparse.Namespace) -> int:
    json_line(
        {
            "type": "run_meta",
            "tool": "listen",
            "mode": "listen",
            "port": args.port,
            "baud": args.baud,
            "bytesize": args.bytesize,
            "parity": args.parity,
            "stopbits": args.stopbits,
            "duration_s": args.listen_seconds,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    chunks = 0
    total = 0
    with configure_port(args) as port:
        port.reset_input_buffer()
        deadline = time.monotonic() + args.listen_seconds
        while time.monotonic() < deadline:
            port.timeout = 0.2
            data = port.read(256)
            if not data:
                continue
            chunks += 1
            total += len(data)
            json_line(
                {
                    "type": "listen_chunk",
                    "seq": chunks,
                    "rx_len": len(data),
                    "rx_hex": data.hex(" "),
                    "elapsed_ms": round((args.listen_seconds - (deadline - time.monotonic())) * 1000.0, 3),
                }
            )
    json_line({"type": "summary", "chunks": chunks, "bytes": total})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RS485 Modbus RTU JSONL probe")
    parser.add_argument("--tool", choices=("pyraw", "minimal"), default="pyraw")
    parser.add_argument("--mode", choices=("single", "batch", "listen"), default="single")
    parser.add_argument("--port", default="/dev/ttyS6")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--bytesize", type=int, default=8)
    parser.add_argument("--parity", default="N")
    parser.add_argument("--stopbits", type=float, default=1)
    parser.add_argument("--timeout-ms", type=float, default=500)
    parser.add_argument("--write-timeout-ms", type=float, default=500)
    parser.add_argument("--target-timeout-ms", type=float, default=None)
    parser.add_argument("--post-tx-delay-ms", type=float, default=None)
    parser.add_argument("--target", default="env-temp")
    parser.add_argument("--targets", default="")
    parser.add_argument("--scenario", choices=tuple(SCENARIOS.keys()), default=None)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--gap-ms", type=float, default=200)
    parser.add_argument("--retry-gap-ms", type=float, default=50)
    parser.add_argument("--extra-retry", type=int, default=0)
    parser.add_argument("--open-mode", choices=("open-close", "keep-open"), default="open-close")
    parser.add_argument("--wait-tx", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--rs485", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--rs485-rts-tx", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--rs485-rts-rx", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--rs485-loopback", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--rs485-delay-before-tx-ms", type=float, default=0.0)
    parser.add_argument("--rs485-delay-before-rx-ms", type=float, default=0.0)
    parser.add_argument("--listen-seconds", type=float, default=30.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.mode == "listen":
        return run_listen(args)
    return run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
