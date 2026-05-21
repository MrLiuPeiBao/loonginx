#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import sys
import time
from typing import Dict, List


def _read_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            raw = handle.read().replace(b"\x00", b" ").decode("utf-8", "ignore").strip()
        return raw or f"[pid={pid}]"
    except OSError:
        return f"[pid={pid}]"


def find_holders(device: str, *, exclude_pid: int | None = None) -> List[Dict[str, object]]:
    holders: List[Dict[str, object]] = []
    seen: set[int] = set()

    for fd_path in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            if os.path.realpath(fd_path) != device:
                continue

            pid = int(fd_path.split("/")[2])
            if pid == exclude_pid or pid in seen:
                continue

            seen.add(pid)
            holders.append(
                {
                    "pid": pid,
                    "cmd": _read_cmdline(pid),
                }
            )
        except (OSError, ValueError):
            continue

    holders.sort(key=lambda item: int(item["pid"]))
    return holders


def terminate_holders(device: str, *, term_wait: float, kill_wait: float) -> Dict[str, object]:
    self_pid = os.getpid()
    before = find_holders(device, exclude_pid=self_pid)
    actions: List[Dict[str, object]] = []

    for holder in before:
        pid = int(holder["pid"])
        try:
            os.kill(pid, signal.SIGTERM)
            actions.append(
                {
                    "pid": pid,
                    "cmd": holder["cmd"],
                    "signal": "SIGTERM",
                    "ok": True,
                }
            )
        except OSError as exc:
            actions.append(
                {
                    "pid": pid,
                    "cmd": holder["cmd"],
                    "signal": "SIGTERM",
                    "ok": False,
                    "error": str(exc),
                }
            )

    if before and term_wait > 0:
        time.sleep(term_wait)

    remaining = find_holders(device, exclude_pid=self_pid)
    for holder in remaining:
        pid = int(holder["pid"])
        try:
            os.kill(pid, signal.SIGKILL)
            actions.append(
                {
                    "pid": pid,
                    "cmd": holder["cmd"],
                    "signal": "SIGKILL",
                    "ok": True,
                }
            )
        except OSError as exc:
            actions.append(
                {
                    "pid": pid,
                    "cmd": holder["cmd"],
                    "signal": "SIGKILL",
                    "ok": False,
                    "error": str(exc),
                }
            )

    if remaining and kill_wait > 0:
        time.sleep(kill_wait)

    after = find_holders(device, exclude_pid=self_pid)
    return {
        "serial_port": device,
        "tty_holders_before": before,
        "tty_holder_actions": actions,
        "tty_holders_after": after,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Terminate processes holding a serial device.")
    parser.add_argument(
        "--device",
        default="/dev/ttyS6",
        help="Serial device to release. Defaults to /dev/ttyS6.",
    )
    parser.add_argument(
        "--term-wait",
        type=float,
        default=1.0,
        help="Seconds to wait after SIGTERM before escalating.",
    )
    parser.add_argument(
        "--kill-wait",
        type=float,
        default=0.5,
        help="Seconds to wait after SIGKILL before re-checking.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    result = terminate_holders(
        args.device,
        term_wait=max(0.0, float(args.term_wait)),
        kill_wait=max(0.0, float(args.kill_wait)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["tty_holders_after"] else 1


if __name__ == "__main__":
    sys.exit(main())
