from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.runtime.plc_bridge import PLCBridge, PLCBridgeError


def _make_settings(**overrides):
    base = {
        "plc_direct_enabled": True,
        "plc_rt_pipe_name": r"\\.\pipe\loonginx-plc-rt",
        "plc_rt_authkey": "loonginx-plc-rt",
        "plc_rt_request_timeout": 5.0,
        "plc_rt_connect_retry_seconds": 0.1,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_bridge_returns_command_execution(monkeypatch):
    bridge = PLCBridge(_make_settings())

    class FakeClient:
        def request(self, message):
            assert message["kind"] == "plc.command.req"
            assert message["body"]["trace_id"] == "trace-1"
            return {
                "body": {
                    "execution": {
                        "response_payload": {"success": True, "result": {"ok": True}},
                        "status_payload": {"status": {"ok": True}},
                        "frames": [{"direction": "TX"}],
                    }
                }
            }

    bridge._client = FakeClient()
    execution = bridge.execute_command(command_type="read_status", request_id="req-1", trace_id="trace-1")
    assert execution.response_payload["success"] is True
    assert execution.status_payload == {"status": {"ok": True}}
    assert execution.frames == ({"direction": "TX"},)


def test_bridge_raises_when_disabled():
    bridge = PLCBridge(_make_settings(plc_direct_enabled=False))
    with pytest.raises(PLCBridgeError):
        bridge.execute_command(command_type="read_status", request_id="req-1")
