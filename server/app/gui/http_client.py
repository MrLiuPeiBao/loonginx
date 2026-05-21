from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class GUIHttpClient:
    def __init__(self, *, base_url: str, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> httpx.Response:
        return await self._client.request(
            method,
            path,
            headers=headers,
            params=params,
            json=json_body,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
