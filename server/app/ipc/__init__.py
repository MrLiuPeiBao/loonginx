"""IPC helpers for PLC bridge and worker processes."""

from .named_pipe import NamedPipeJsonClient, NamedPipeJsonServer, IPCError, IPCUnavailableError
from .protocol import IPC_VERSION, decode_message, encode_message, make_message

__all__ = [
    "IPC_VERSION",
    "IPCError",
    "IPCUnavailableError",
    "NamedPipeJsonClient",
    "NamedPipeJsonServer",
    "decode_message",
    "encode_message",
    "make_message",
]
