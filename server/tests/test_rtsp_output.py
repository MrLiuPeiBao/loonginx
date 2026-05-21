from app.services.rtsp_output import (
    RtspOutputConfig,
    _parse_rtsp_url,
    _build_gst_launch,
    _build_encoder_props,
    _resolve_server_bind_host,
)


def test_encoder_props_are_discarded_after_encoder_fallback() -> None:
    config = RtspOutputConfig(
        output_url='rtsp://127.0.0.1:8555/yolo',
        width=640,
        height=360,
        fps=20,
        backend='gstreamer',
        encoder='nvh264enc',
        encoder_props='preset=p1 tune=ultra-low-latency',
    )

    props = _build_encoder_props(config, 'x264enc', 20)

    assert 'preset=p1' not in props
    assert 'speed-preset=ultrafast' in props


def test_encoder_props_are_kept_when_encoder_matches() -> None:
    config = RtspOutputConfig(
        output_url='rtsp://127.0.0.1:8555/yolo',
        width=640,
        height=360,
        fps=20,
        backend='gstreamer',
        encoder='x264enc',
        encoder_props='speed-preset=superfast tune=zerolatency',
    )

    props = _build_encoder_props(config, 'x264enc', 20)

    assert props == 'speed-preset=superfast tune=zerolatency'


def test_rtsp_output_binds_all_interfaces_for_lan_advertised_host() -> None:
    assert _resolve_server_bind_host('192.168.0.100') == '0.0.0.0'


def test_rtsp_output_keeps_loopback_bind_host() -> None:
    assert _resolve_server_bind_host('127.0.0.1') == '127.0.0.1'


def test_gst_launch_uses_nv12_for_nvh264enc() -> None:
    config = RtspOutputConfig(
        output_url='rtsp://127.0.0.1:8555/yolo',
        width=960,
        height=540,
        fps=15,
        backend='gstreamer',
        encoder='nvh264enc',
        encoder_props='preset=p1 tune=low-latency',
    )

    launch = _build_gst_launch(config)

    assert 'video/x-raw,format=NV12' in launch
    assert 'nvh264enc preset=p1 tune=low-latency' in launch


def test_gst_launch_uses_i420_for_x264enc() -> None:
    config = RtspOutputConfig(
        output_url='rtsp://127.0.0.1:8555/yolo',
        width=960,
        height=540,
        fps=15,
        backend='gstreamer',
        encoder='x264enc',
        encoder_props='',
    )

    launch = _build_gst_launch(config)

    assert 'video/x-raw,format=I420' in launch
    assert 'x264enc' in launch


def test_parse_rtsp_url_defaults_to_8555_when_port_missing() -> None:
    host, port, mount = _parse_rtsp_url('rtsp://192.168.0.100/yolo')

    assert host == '192.168.0.100'
    assert port == 8555
    assert mount == '/yolo'
