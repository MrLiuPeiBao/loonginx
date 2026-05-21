from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from app.core.config import Settings
from app.ipc import IPCUnavailableError, NamedPipeJsonClient, make_message
from app.services.cableway_plc import CablewayCommandExecution
from app.services.plc_timing import emit_plc_timing


class PLCBridgeError(RuntimeError):
    """Raised when communication with PLC-RT fails."""


class PLCBridge:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = self._build_client(settings)

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._client = self._build_client(settings)

    async def apply_settings_async(self, settings: Settings) -> None:
        self.apply_settings(settings)

    def execute_command(
        self,
        *,
        command_type: str,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        device_id: Optional[str] = None,
        command_code: Optional[int] = None,
        pulse: bool = True,
        params: Optional[Dict[str, Any]] = None,
    ) -> CablewayCommandExecution:
        if not self._settings.plc_direct_enabled:
            raise PLCBridgeError("PLC direct mode is disabled")

        message_id = str(request_id or "plc-command")
        message = make_message(
            kind="plc.command.req",
            message_id=message_id,
            source="api",
            body={
                "type": command_type,
                "request_id": request_id,
                "trace_id": trace_id,
                "device_id": device_id,
                "command_code": command_code,
                "pulse": bool(pulse),
                "params": dict(params or {}),
            },
        )
        emit_plc_timing(
            "bridge.request_start",
            request_id=request_id,
            trace_id=trace_id,
            job_kind="command",
            priority=0 if command_type in {"control", "estop"} else 10,
            command_type=command_type,
            command_code=command_code,
            device_id=device_id,
            bridge_stage="request_start",
        )
        try:
            response = self._client.request(message)
        except IPCUnavailableError as exc:
            emit_plc_timing(
                "bridge.request_error",
                request_id=request_id,
                trace_id=trace_id,
                job_kind="command",
                command_type=command_type,
                command_code=command_code,
                device_id=device_id,
                bridge_stage="request_error",
                error=str(exc),
            )
            raise PLCBridgeError(str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive
            emit_plc_timing(
                "bridge.request_error",
                request_id=request_id,
                trace_id=trace_id,
                job_kind="command",
                command_type=command_type,
                command_code=command_code,
                device_id=device_id,
                bridge_stage="request_error",
                error=str(exc),
            )
            raise PLCBridgeError(f"PLC bridge request failed: {exc}") from exc

        body = response.get("body")
        if not isinstance(body, dict):
            raise PLCBridgeError("PLC bridge response missing body")

        execution_payload = body.get("execution")
        if not isinstance(execution_payload, dict):
            raise PLCBridgeError("PLC bridge response missing execution payload")

        response_payload = execution_payload.get("response_payload")
        if not isinstance(response_payload, dict):
            raise PLCBridgeError("PLC bridge response missing response_payload")

        status_payload = execution_payload.get("status_payload")
        if status_payload is not None and not isinstance(status_payload, dict):
            status_payload = None

        frames = execution_payload.get("frames")
        normalized_frames = tuple(
            dict(item) for item in frames if isinstance(item, dict)
        ) if isinstance(frames, list) else ()

        emit_plc_timing(
            "bridge.request_end",
            request_id=request_id,
            trace_id=trace_id,
            job_kind="command",
            command_type=command_type,
            command_code=command_code,
            device_id=device_id,
            bridge_stage="request_end",
            result=str(response.get("body", {}).get("result") or ""),
        )

        return CablewayCommandExecution(
            response_payload=dict(response_payload),
            status_payload=dict(status_payload) if isinstance(status_payload, dict) else None,
            frames=normalized_frames,
        )

    async def execute_command_async(
        self,
        *,
        command_type: str,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        device_id: Optional[str] = None,
        command_code: Optional[int] = None,
        pulse: bool = True,
        params: Optional[Dict[str, Any]] = None,
    ) -> CablewayCommandExecution:
        return await asyncio.to_thread(
            self.execute_command,
            command_type=command_type,
            request_id=request_id,
            trace_id=trace_id,
            device_id=device_id,
            command_code=command_code,
            pulse=pulse,
            params=params,
        )

    def _build_client(self, settings: Settings) -> NamedPipeJsonClient:
        return NamedPipeJsonClient(
            address=str(settings.plc_rt_pipe_name),
            authkey=str(settings.plc_rt_authkey or "loonginx-plc-rt").encode("utf-8"),
            request_timeout=float(settings.plc_rt_request_timeout or 5.0),
            connect_retry_seconds=float(settings.plc_rt_connect_retry_seconds or 2.0),
        )
