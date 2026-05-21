from __future__ import annotations


def encode_length_prefixed(payload: bytes) -> bytes:
    data = bytes(payload or b"")
    return len(data).to_bytes(4, byteorder="little", signed=False) + data


def decode_length_prefixed(frame: bytes) -> bytes:
    raw = bytes(frame or b"")
    if len(raw) < 4:
        raise ValueError("Frame too short")
    expected = int.from_bytes(raw[:4], byteorder="little", signed=False)
    payload = raw[4:]
    if len(payload) != expected:
        raise ValueError(f"Frame payload length mismatch expected={expected} actual={len(payload)}")
    return payload
