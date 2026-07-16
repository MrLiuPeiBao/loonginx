from __future__ import annotations

import asyncio

import httpx

from app.gui.app import MonitoringGUI


def test_extract_rows_accepts_page_payload():
    rows = [{'device_id': 'bms-1'}, {'device_id': 'bms-2'}]

    assert MonitoringGUI._extract_rows({'total': 2, 'items': rows}) == rows


def test_check_bms_cell_voltages_accepts_page_payload():
    gui = MonitoringGUI.__new__(MonitoringGUI)
    gui._bms_alert_tracker = {}
    posted: list[tuple[object, ...]] = []
    gui._post_bms_alarm = lambda *args: posted.append(args)

    alerts = gui._check_bms_cell_voltages(
        {
            'total': 1,
            'items': [
                {
                    'device_id': 'bms-1',
                    'location': 'field',
                    'cell_voltages': [3.42, 3.31],
                    'timestamp': '2026-04-28T16:00:00',
                }
            ],
        }
    )

    assert len(alerts) == 1
    assert alerts[0]['device_id'] == 'bms-1'
    assert alerts[0]['min_voltage'] == 3.31
    assert posted == [('bms-1', 'field', 3.31, 3.35)]


def test_refresh_data_task_handles_read_error_without_reraising():
    gui = MonitoringGUI.__new__(MonitoringGUI)
    gui._refresh_in_progress = True
    gui._get_page_snapshot = lambda keys: {}
    gui._build_page_params = lambda key: {}
    handled: list[Exception] = []
    gui._handle_refresh_error = lambda exc: handled.append(exc)

    async def raise_read_error(*args, **kwargs):
        raise httpx.ReadError('server disconnected')

    gui._refresh_data_via_aggregate = raise_read_error

    asyncio.run(gui._refresh_data_task())

    assert len(handled) == 1
    assert isinstance(handled[0], httpx.ReadError)
    assert gui._refresh_in_progress is False


def test_refresh_task_skips_when_refresh_already_running():
    gui = MonitoringGUI.__new__(MonitoringGUI)
    gui._refresh_in_progress = True
    statuses: list[str] = []
    spawned: list[object] = []
    gui._update_status = statuses.append
    gui._spawn_async = spawned.append

    gui._spawn_refresh_task()

    assert spawned == []
    assert statuses == ['数据刷新中，跳过重复刷新']


def test_apply_audio_metrics_accepts_api_metric_list():
    gui = MonitoringGUI.__new__(MonitoringGUI)
    gui._audio_metrics_buffer = [{'wav_rms': 1.0}]
    updated: list[bool] = []
    cleared: list[bool] = []
    gui._update_audio_chart = lambda: updated.append(True)
    gui._clear_audio_chart = lambda: cleared.append(True)

    gui._apply_audio_metrics(
        [
            {'wav_rms': 128.0, 'wav_peak': 512.0},
            {'wav_rms': 129.0, 'wav_peak': 560.0},
            'invalid',
        ]
    )

    assert gui._audio_metrics_buffer == [
        {'wav_rms': 128.0, 'wav_peak': 512.0},
        {'wav_rms': 129.0, 'wav_peak': 560.0},
    ]
    assert updated == [True]
    assert cleared == []


def test_apply_audio_metrics_keeps_latest_two_hundred_points():
    gui = MonitoringGUI.__new__(MonitoringGUI)
    gui._audio_metrics_buffer = []
    gui._update_audio_chart = lambda: None
    gui._clear_audio_chart = lambda: None

    gui._apply_audio_metrics([{'wav_rms': float(idx)} for idx in range(250)])

    assert len(gui._audio_metrics_buffer) == 200
    assert gui._audio_metrics_buffer[0]['wav_rms'] == 50.0
    assert gui._audio_metrics_buffer[-1]['wav_rms'] == 249.0


def test_convert_gui_refresh_payload_uses_section_data_and_logs_errors():
    gui = MonitoringGUI.__new__(MonitoringGUI)
    logs: list[str] = []
    gui._append_log = logs.append
    gui._check_bms_cell_voltages = lambda payload: [{'device_id': 'bms-1'}]

    aggregate = {
        'sections': {
            'sensors': {'ok': True, 'data': {'total': 1, 'items': [{'device_id': 's-1'}]}},
            'bms': {'ok': True, 'data': {'total': 1, 'items': [{'device_id': 'b-1'}]}},
            'rfid': {'ok': True, 'data': {'total': 0, 'items': []}},
            'commands': {'ok': True, 'data': {'total': 0, 'items': []}},
            'configs': {'ok': True, 'data': {'total': 0, 'items': []}},
            'alarms': {'ok': True, 'data': {'total': 0, 'items': []}},
            'images': {'ok': False, 'error': 'images timeout', 'data': {'total': 0, 'items': []}},
            'audio': {'ok': True, 'data': {'total': 0, 'items': []}},
            'audio_metrics': {'ok': True, 'data': {'total': 0, 'items': []}},
        }
    }

    payload = gui._convert_gui_refresh_payload(aggregate, {'sensors': 0})

    assert payload is not None
    assert payload['sensors']['items'][0]['device_id'] == 's-1'
    assert payload['bms_alerts'] == [{'device_id': 'bms-1'}]
    assert any('images timeout' in line for line in logs)
