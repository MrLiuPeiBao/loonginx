from __future__ import annotations

from app.core.config import Settings


def test_media_gateway_resolve_rtsp_inputs_prefers_relay():
    settings = Settings(
        _env_file=None,
        yolo_rtsp_input='rtsp://192.168.0.101:554/Streaming/Channels/101',
        audio_rtsp_input='rtsp://192.168.0.101:554/Streaming/Channels/101',
        media_gateway_enabled=True,
        media_gateway_type='go2rtc',
        media_gateway_relay_rtsp='rtsp://127.0.0.1:8554/cam01',
        media_gateway_control_panel_rtsp='rtsp://127.0.0.1:8554/cam01',
    )

    assert settings.resolve_yolo_rtsp_input() == 'rtsp://127.0.0.1:8554/cam01'
    assert settings.resolve_audio_rtsp_input() == 'rtsp://127.0.0.1:8554/cam01'
    assert settings.resolve_control_panel_rtsp_input() == 'rtsp://127.0.0.1:8554/cam01'


def test_media_gateway_resolve_rtsp_inputs_can_disable_rewrite():
    settings = Settings(
        _env_file=None,
        yolo_rtsp_input='rtsp://192.168.0.101:554/Streaming/Channels/101',
        audio_rtsp_input='rtsp://192.168.0.101:554/Streaming/Channels/101',
        media_gateway_enabled=True,
        media_gateway_type='mediamtx',
        media_gateway_relay_rtsp='rtsp://127.0.0.1:8554/cam01',
        media_gateway_rewrite_yolo_input=False,
        media_gateway_rewrite_audio_input=False,
    )

    assert settings.resolve_yolo_rtsp_input() == 'rtsp://192.168.0.101:554/Streaming/Channels/101'
    assert settings.resolve_audio_rtsp_input() == 'rtsp://192.168.0.101:554/Streaming/Channels/101'
