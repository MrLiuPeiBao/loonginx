#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from communication.serial_manager import SerialManager
from config import BMS_CONFIG, SERIAL_CONFIG
from sensors.bms_sensors import BMSSensor


def _load_release_helper():
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from release_ttys6 import terminate_holders  # local helper script

    return terminate_holders


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Release ttyS6 holders and immediately probe BMS once."
    )
    parser.add_argument(
        "--device",
        default=str(SERIAL_CONFIG["port"]),
        help="Serial device to release before probing. Defaults to configured BMS serial port.",
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

    terminate_holders = _load_release_helper()
    release_result = terminate_holders(
        args.device,
        term_wait=max(0.0, float(args.term_wait)),
        kill_wait=max(0.0, float(args.kill_wait)),
    )

    probe_result: dict[str, object]
    serial_manager = SerialManager(**SERIAL_CONFIG)
    try:
        bms = BMSSensor(serial_manager, BMS_CONFIG)
        data = bms.read_all_data()
        ok = any(value is not None for value in data.get("data", {}).values())
        probe_result = {"ok": ok, "data": data}
    except Exception:
        probe_result = {"ok": False, "error": traceback.format_exc()}
    finally:
        serial_manager.close_all()

    result = {
        "serial_port": args.device,
        "release_result": release_result,
        "bms_probe": probe_result,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not release_result["tty_holders_after"] else 1


if __name__ == "__main__":
    sys.exit(main())
