from __future__ import annotations

import asyncio
from datetime import datetime

from app.api.routes import (
    _audio_read_from_row,
    _image_read_from_row,
    get_audio_data as get_audio_data_route,
    get_image_data as get_image_data_route,
)
from app.db.models import AudioData, ImageData


def test_image_read_can_omit_base64_payload():
    row = ImageData(
        id=12,
        timestamp=datetime(2026, 5, 6, 17, 0, 0),
        device_id='camera-01',
        image_name='snapshot.jpg',
        image_data='large-base64-image',
        location='gate-a',
    )

    payload = _image_read_from_row(row, include_data=False)

    assert payload.id == 12
    assert payload.image_name == 'snapshot.jpg'
    assert payload.image_data == ''


def test_audio_read_can_omit_base64_payload():
    row = AudioData(
        id=34,
        timestamp=datetime(2026, 5, 6, 17, 0, 0),
        device_id='mic-01',
        audio_name='audio.wav',
        audio_data=b'large-audio-bytes',
        location='gate-a',
    )

    payload = _audio_read_from_row(row, include_data=False)

    assert payload.id == 34
    assert payload.audio_name == 'audio.wav'
    assert payload.audio_data == ''


def test_image_detail_route_awaits_data_service():
    row = ImageData(
        id=12,
        timestamp=datetime(2026, 5, 6, 17, 0, 0),
        device_id='camera-01',
        image_name='snapshot.jpg',
        image_data='large-base64-image',
        location='gate-a',
    )

    class Service:
        async def get_image_data(self, image_id: int):
            assert image_id == 12
            return row

    payload = asyncio.run(get_image_data_route(12, data_service=Service()))

    assert payload.id == 12
    assert payload.image_data == 'large-base64-image'


def test_audio_detail_route_awaits_data_service():
    row = AudioData(
        id=34,
        timestamp=datetime(2026, 5, 6, 17, 0, 0),
        device_id='mic-01',
        audio_name='audio.wav',
        audio_data=b'RIFFdata',
        location='gate-a',
    )

    class Service:
        async def get_audio_data(self, audio_id: int):
            assert audio_id == 34
            return row

    payload = asyncio.run(get_audio_data_route(34, data_service=Service()))

    assert payload.id == 34
    assert payload.audio_data == 'UklGRmRhdGE='
