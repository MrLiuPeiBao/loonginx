from __future__ import annotations

import logging
from typing import Any, Dict, Optional


DEFAULT_IO_COMMANDS: Dict[str, Dict[bool, bytes]] = {
    "running": {
        True: bytes.fromhex("19 05 00 00 FF 00 8F E2"),
        False: bytes.fromhex("19 05 00 00 00 00 CE 12"),
    },
    "communication": {
        True: bytes.fromhex("19 05 00 01 FF 00 DE 22"),
        False: bytes.fromhex("19 05 00 01 00 00 9F D2"),
    },
    "charge": {
        True: bytes.fromhex("19 05 00 02 FF 00 2E 22"),
        False: bytes.fromhex("19 05 00 02 00 00 6F D2"),
    },
    "camera": {
        True: bytes.fromhex("19 05 00 03 FF 00 7F E2"),
        False: bytes.fromhex("19 05 00 03 00 00 3E 12"),
    },
    "obstacle": {
        True: bytes.fromhex("19 05 00 04 FF 00 CE 23"),
        False: bytes.fromhex("19 05 00 04 00 00 8F D3"),
    },
    "alarm": {
        True: bytes.fromhex("19 05 00 05 FF 00 9F E3"),
        False: bytes.fromhex("19 05 00 05 00 00 DE 13"),
    },
}


class IOBoard:
    """Small wrapper for the Modbus RTU IO board outputs."""

    def __init__(self, serial_manager: Any, config: Optional[dict] = None):
        self.serial_manager = serial_manager
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", True))
        self.commands = dict(self.config.get("commands") or DEFAULT_IO_COMMANDS)
        self._output_state: Dict[str, bool] = {}

    def set_output(
        self,
        name: str,
        enabled: bool,
        *,
        force: bool = False,
        wait_for_tx_complete: bool = True,
        response_timeout: Optional[float] = None,
    ) -> bool:
        if not self.enabled:
            return False

        normalized = str(name).strip().lower()
        command_pair = self.commands.get(normalized)
        if not command_pair:
            logging.error("Unknown IO output name=%s", name)
            return False

        target = bool(enabled)
        if not force and self._output_state.get(normalized) is target:
            return True

        command = command_pair[target]
        try:
            response = self.serial_manager.send_raw_command_with_options(
                command,
                wait_for_tx_complete=wait_for_tx_complete,
                response_timeout=response_timeout,
            )
        except Exception as exc:
            logging.error("IO output failed name=%s state=%s error=%s", normalized, target, exc)
            return False

        if not response:
            logging.error("IO output no response name=%s state=%s", normalized, target)
            return False

        self._output_state[normalized] = target
        logging.info("IO output set name=%s state=%s", normalized, target)
        return True

    def set_outputs(
        self,
        states: Dict[str, bool],
        *,
        force: bool = False,
        wait_for_tx_complete: bool = True,
        response_timeout: Optional[float] = None,
    ) -> bool:
        ok = True
        for name, enabled in states.items():
            ok = self.set_output(
                name,
                enabled,
                force=force,
                wait_for_tx_complete=wait_for_tx_complete,
                response_timeout=response_timeout,
            ) and ok
        return ok

    def get_cached_state(self, name: str) -> Optional[bool]:
        return self._output_state.get(str(name).strip().lower())
