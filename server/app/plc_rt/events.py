from __future__ import annotations

from typing import Any, Dict, Optional

from app.ipc.protocol import make_message


def build_command_response(
    *,
    message_id: str,
    result: str,
    execution: Dict[str, Any],
    error: Optional[str] = None,
) -> Dict[str, Any]:
    return make_message(
        kind="plc.command.resp",
        message_id=message_id,
        source="plc-rt",
        body={
            "result": result,
            "error": error,
            "execution": execution,
        },
    )


def build_status_snapshot(*, seq: int, reason: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return make_message(
        kind="plc.status.snapshot",
        message_id=f"seq-{seq}",
        source="plc-rt",
        body={
            "seq": seq,
            "reason": reason,
            "snapshot": snapshot,
        },
    )


def build_fault_transition(*, seq: int, raised: list[str], cleared: list[str]) -> Dict[str, Any]:
    return make_message(
        kind="plc.fault.transition",
        message_id=f"fault-{seq}",
        source="plc-rt",
        body={
            "snapshot_seq": seq,
            "raised": list(raised),
            "cleared": list(cleared),
        },
    )
