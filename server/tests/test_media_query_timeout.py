from __future__ import annotations

import asyncio
from datetime import datetime

from app.api.routes import list_audio_data, list_image_data
from app.db.models import AudioData, ImageData


class _SlowMediaDataService:
    async def count_image_data(self, **kwargs):
        await asyncio.sleep(0.2)
        return 1

    async def list_image_data(self, **kwargs):
        await asyncio.sleep(0.2)
        return [
            ImageData(
                id=1,
                timestamp=datetime(2026, 5, 7, 18, 0, 0),
                device_id='camera-1',
                image_name='a.jpg',
                image_data='aW1hZ2U=',
                location='gate',
            )
        ]

    async def count_audio_data(self, **kwargs):
        await asyncio.sleep(0.2)
        return 1

    async def list_audio_data(self, **kwargs):
        await asyncio.sleep(0.2)
        return [
            AudioData(
                id=2,
                timestamp=datetime(2026, 5, 7, 18, 0, 0),
                device_id='mic-1',
                audio_name='a.wav',
                audio_data=b'RIFFdata',
                location='gate',
            )
        ]


def test_list_image_data_returns_timeout_status_when_query_exceeds_deadline():
    payload = asyncio.run(
        list_image_data(
            start=None,
            end=None,
            device_id=None,
            location=None,
            limit=None,
            offset=0,
            include_data=False,
            timeout_seconds=0.1,
            data_service=_SlowMediaDataService(),
        )
    )

    assert payload.status == 'timeout'
    assert payload.total == 0
    assert payload.items == []


def test_list_audio_data_returns_timeout_status_when_query_exceeds_deadline():
    payload = asyncio.run(
        list_audio_data(
            start=None,
            end=None,
            device_id=None,
            location=None,
            limit=None,
            offset=0,
            include_data=False,
            timeout_seconds=0.1,
            data_service=_SlowMediaDataService(),
        )
    )

    assert payload.status == 'timeout'
    assert payload.total == 0
    assert payload.items == []


def test_list_image_data_passes_include_data_flag_to_service():
    observed: dict[str, object] = {}

    class Service:
        async def count_image_data(self, **kwargs):
            return 0

        async def list_image_data(self, **kwargs):
            observed.update(kwargs)
            return []

    payload = asyncio.run(
        list_image_data(
            start=None,
            end=None,
            device_id=None,
            location=None,
            limit=10,
            offset=0,
            include_data=False,
            timeout_seconds=1.0,
            data_service=Service(),
        )
    )

    assert payload.status == 'ok'
    assert observed['include_data'] is False


def test_list_audio_data_passes_include_data_flag_to_service():
    observed: dict[str, object] = {}

    class Service:
        async def count_audio_data(self, **kwargs):
            return 0

        async def list_audio_data(self, **kwargs):
            observed.update(kwargs)
            return []

    payload = asyncio.run(
        list_audio_data(
            start=None,
            end=None,
            device_id=None,
            location=None,
            limit=10,
            offset=0,
            include_data=False,
            timeout_seconds=1.0,
            data_service=Service(),
        )
    )

    assert payload.status == 'ok'
    assert observed['include_data'] is False
