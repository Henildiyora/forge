from __future__ import annotations

from typing import cast

import httpx


class PrometheusClient:
    """Small async Prometheus client used by the Watchman agent."""

    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_seconds,
        )

    async def query_range(
        self,
        query: str,
        start: str,
        end: str,
        step: str,
    ) -> dict[str, object]:
        response = await self._client.get(
            "/api/v1/query_range",
            params={
                "query": query,
                "start": start,
                "end": end,
                "step": step,
            },
        )
        response.raise_for_status()
        return cast(dict[str, object], response.json())

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
