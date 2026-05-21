from __future__ import annotations

import time
from multiprocessing.connection import Client, Listener
from typing import Callable, Dict, Optional
import logging

from .protocol import decode_message, encode_message

logger = logging.getLogger(__name__)


class IPCError(RuntimeError):
    """Base IPC error."""


class IPCUnavailableError(IPCError):
    """Raised when IPC endpoint is unavailable."""


class NamedPipeJsonClient:
    def __init__(
        self,
        *,
        address: str,
        authkey: bytes,
        request_timeout: float = 5.0,
        connect_retry_seconds: float = 2.0,
    ) -> None:
        self._address = address
        self._authkey = bytes(authkey or b"")
        self._request_timeout = max(0.1, float(request_timeout))
        self._connect_retry_seconds = max(0.0, float(connect_retry_seconds))

    def request(self, message: Dict[str, object]) -> Dict[str, object]:
        deadline = time.monotonic() + self._connect_retry_seconds
        last_error: Optional[BaseException] = None
        while True:
            try:
                with Client(address=self._address, family="AF_PIPE", authkey=self._authkey) as connection:
                    connection.send_bytes(encode_message(message))
                    if not connection.poll(self._request_timeout):
                        raise IPCUnavailableError(
                            f"Timed out waiting for IPC response after {self._request_timeout:.1f}s"
                        )
                    return decode_message(connection.recv_bytes())
            except IPCUnavailableError:
                raise
            except Exception as exc:  # pragma: no cover - OS dependent
                last_error = exc
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
        raise IPCUnavailableError(f"IPC endpoint unavailable: {last_error}") from last_error


class NamedPipeJsonServer:
    def __init__(self, *, address: str, authkey: bytes) -> None:
        self._address = address
        self._authkey = bytes(authkey or b"")

    def serve_forever(
        self,
        *,
        handler: Callable[[Dict[str, object]], Dict[str, object]],
        should_stop: Callable[[], bool],
    ) -> None:
        with Listener(address=self._address, family="AF_PIPE", authkey=self._authkey) as listener:
            while not should_stop():
                try:
                    with listener.accept() as connection:
                        message = decode_message(connection.recv_bytes())
                        response = handler(message)
                        try:
                            connection.send_bytes(encode_message(response))
                        except (BrokenPipeError, OSError):
                            logger.debug("IPC client disconnected before response", exc_info=True)
                except Exception:
                    if should_stop():
                        break
                    raise
