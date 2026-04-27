from __future__ import annotations

from typing import Any

import httpx
import pytest

from forge.agents.watchman.agent import WatchmanAgent
from forge.agents.watchman.loki_client import LokiClient
from forge.agents.watchman.prometheus_client import PrometheusClient
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus
from tests.conftest import FakeRedisStreamClient


class StubPrometheusClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = responses

    async def query_range(
        self,
        query: str,
        start: str,
        end: str,
        step: str,
    ) -> dict[str, object]:
        del query, start, end, step
        return self._responses.pop(0)


class StubLokiClient:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response

    async def query_range(
        self,
        query: str,
        start: str,
        end: str,
    ) -> dict[str, object]:
        del query, start, end
        return self._response


def _prometheus_response(value: float) -> dict[str, object]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"service": "api"},
                    "values": [["1710000000", f"{value:.3f}"]],
                }
            ],
        },
    }


def _loki_response(count: int) -> dict[str, object]:
    values = [[str(1710000000 + index), f"error line {index}"] for index in range(count)]
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [{"stream": {"service": "api"}, "values": values}],
        },
    }


@pytest.fixture
def watchman_agent(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> WatchmanAgent:
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    return WatchmanAgent(
        settings=test_settings,
        message_bus=bus,
        prometheus_client=StubPrometheusClient(
            [
                _prometheus_response(0.01),
                _prometheus_response(120.0),
                _prometheus_response(0.0),
            ]
        ),
        loki_client=StubLokiClient(_loki_response(1)),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prometheus_client_query_range_uses_expected_endpoint() -> None:
    captured_request: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["url"] = str(request.url)
        return httpx.Response(
            status_code=200,
            json={"status": "success", "data": {"resultType": "matrix", "result": []}},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://prometheus.test",
    ) as http_client:
        client = PrometheusClient(base_url="http://prometheus.test", http_client=http_client)
        result = await client.query_range("up", "start-ts", "end-ts", "60s")

    assert result["status"] == "success"
    assert (
        captured_request["url"]
        == "http://prometheus.test/api/v1/query_range?query=up&start=start-ts&end=end-ts&step=60s"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_loki_client_query_range_uses_expected_endpoint() -> None:
    captured_request: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["url"] = str(request.url)
        return httpx.Response(
            status_code=200,
            json={"status": "success", "data": {"resultType": "streams", "result": []}},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://loki.test",
    ) as http_client:
        client = LokiClient(base_url="http://loki.test", http_client=http_client)
        result = await client.query_range('{service="api"}', "start-ts", "end-ts")

    assert result["status"] == "success"
    assert "http://loki.test/loki/api/v1/query_range?" in captured_request["url"]
    assert "query=%7Bservice%3D%22api%22%7D" in captured_request["url"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_watchman_reports_healthy_service(
    watchman_agent: WatchmanAgent,
) -> None:
    event = SwarmEvent(
        type=EventType.HEALTH_CHECK_TRIGGERED,
        task_id="monitor-1",
        source_agent="captain",
        target_agent="watchman",
        payload={"service": "api", "namespace": "default"},
    )

    result = await watchman_agent.process_event(event)

    assert result is not None
    assert result.type == EventType.TASK_COMPLETED
    assert result.payload["service"] == "api"
    assert result.payload["anomalies"] == []
    assert result.metadata["anomaly_count"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_watchman_emits_anomaly_detected_when_thresholds_are_exceeded(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> None:
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    agent = WatchmanAgent(
        settings=test_settings,
        message_bus=bus,
        prometheus_client=StubPrometheusClient(
            [
                _prometheus_response(0.22),
                _prometheus_response(980.0),
                _prometheus_response(3.0),
            ]
        ),
        loki_client=StubLokiClient(_loki_response(7)),
    )
    event = SwarmEvent(
        type=EventType.HEALTH_CHECK_TRIGGERED,
        task_id="monitor-2",
        source_agent="captain",
        target_agent="watchman",
        payload={"service": "api", "namespace": "default"},
    )

    result = await agent.process_event(event)

    assert result is not None
    assert result.type == EventType.ANOMALY_DETECTED
    assert len(result.payload["anomalies"]) == 4
    assert result.metadata["anomaly_count"] == 4


@pytest.mark.unit
@pytest.mark.asyncio
async def test_watchman_requires_a_service_identifier(
    watchman_agent: WatchmanAgent,
) -> None:
    event = SwarmEvent(
        type=EventType.HEALTH_CHECK_TRIGGERED,
        task_id="monitor-3",
        source_agent="captain",
        target_agent="watchman",
        payload={"namespace": "default"},
    )

    result = await watchman_agent.process_event(event)

    assert result is not None
    assert result.type == EventType.TASK_FAILED
    assert result.payload["error"] == "missing_service"
