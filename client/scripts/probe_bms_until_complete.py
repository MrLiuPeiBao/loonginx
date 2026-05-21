#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

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


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return all(_is_present(item) for item in value)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Keep probing BMS until every configured field has a response."
    )
    parser.add_argument(
        "--device",
        default=str(SERIAL_CONFIG["port"]),
        help="Serial device to release and probe. Defaults to configured BMS serial port.",
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
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1.0,
        help="Seconds to wait between incomplete attempts.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    terminate_holders = _load_release_helper()

    serial_config = dict(SERIAL_CONFIG)
    serial_config["port"] = args.device
    expected_keys = tuple(BMS_CONFIG["registers"].keys())

    attempts = 0
    final_result: dict[str, Any] | None = None

    while True:
        attempts += 1
        release_result = terminate_holders(
            args.device,
            term_wait=max(0.0, float(args.term_wait)),
            kill_wait=max(0.0, float(args.kill_wait)),
        )

        serial_manager = SerialManager(**serial_config)
        try:
            bms = BMSSensor(serial_manager, BMS_CONFIG)
            data = bms.read_all_data()
            fields = data.get("data", {})
            missing_keys = [key for key in expected_keys if not _is_present(fields.get(key))]
            complete = not missing_keys
            final_result = {
                "attempt": attempts,
                "serial_port": args.device,
                "release_result": release_result,
                "bms_probe": {
                    "ok": complete,
                    "missing_keys": missing_keys,
                    "data": data,
                },
            }
        except Exception:
            final_result = {
                "attempt": attempts,
                "serial_port": args.device,
                "release_result": release_result,
                "bms_probe": {
                    "ok": False,
                    "missing_keys": list(expected_keys),
                    "error": traceback.format_exc(),
                },
            }
        finally:
            serial_manager.close_all()

        print(json.dumps(final_result, ensure_ascii=False), flush=True)
        if final_result["bms_probe"]["ok"]:
            print(json.dumps(final_result, ensure_ascii=False, indent=2))
            return 0

        time.sleep(max(0.0, float(args.sleep_seconds)))


if __name__ == "__main__":
    sys.exit(main())
