from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, Field

from forge.agents.base import BaseAgent
from forge.agents.watchman.loki_client import LokiClient
from forge.agents.watchman.prometheus_client import PrometheusClient
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus


class SupportsPrometheusQuery(Protocol):
    async def query_range(
        self,
        query: str,
        start: str,
        end: str,
        step: str,
    ) -> dict[str, object]: ...


class SupportsLokiQuery(Protocol):
    async def query_range(
        self,
        query: str,
        start: str,
        end: str,
    ) -> dict[str, object]: ...


class MonitoringSnapshot(BaseModel):
    """Aggregated observability data for a single service."""

    service: str = Field(description="Logical service name under evaluation.")
    namespace: str = Field(description="Kubernetes namespace used for metric filters.")
    window_minutes: int = Field(ge=1, description="Lookback window used for the evaluation.")
    error_rate: float = Field(ge=0.0, description="Maximum observed error rate.")
    latency_p95_ms: float = Field(ge=0.0, description="Maximum observed p95 latency in ms.")
    restart_count: float = Field(ge=0.0, description="Observed restart count in the window.")
    error_log_count: int = Field(ge=0, description="Observed count of error-like log lines.")
    anomalies: list[str] = Field(
        default_factory=list,
        description="Human-readable anomaly descriptions.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting the anomaly assessment.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the monitoring assessment.",
    )


class WatchmanAgent(BaseAgent):
    """Observability agent responsible for monitoring and anomaly detection."""

    agent_name = "watchman"

    def __init__(
        self,
        settings: Settings,
        message_bus: MessageBus,
        prometheus_client: SupportsPrometheusQuery | None = None,
        loki_client: SupportsLokiQuery | None = None,
    ) -> None:
        super().__init__(settings, message_bus)
        self.prometheus = prometheus_client or PrometheusClient(base_url=settings.prometheus_url)
        self.loki = loki_client or LokiClient(base_url=settings.loki_url)

    async def monitor_service(
        self,
        *,
        service: str,
        namespace: str,
        lookback_minutes: int = 15,
        error_rate_threshold: float = 0.05,
        latency_threshold_ms: float = 750.0,
        restart_threshold: float = 1.0,
        error_log_threshold: int = 3,
    ) -> MonitoringSnapshot:
        """Collect metrics and logs for a service and detect anomalies."""

        window_end = datetime.now(UTC)
        window_start = window_end - timedelta(minutes=lookback_minutes)
        start = window_start.isoformat()
        end = window_end.isoformat()
        step = "60s"

        error_rate_query = (
            f'sum(rate(http_requests_total{{namespace="{namespace}",service="{service}",'
            'status=~"5.."}[5m])) / '
            f'clamp_min(sum(rate(http_requests_total{{namespace="{namespace}",service="{service}"}}'
            '[5m])), 1)'
        )
        latency_query = (
            "histogram_quantile(0.95, "
            f'sum(rate(http_request_duration_seconds_bucket{{namespace="{namespace}",'
            f'service="{service}"}}[5m])) by (le)) * 1000'
        )
        restart_query = (
            "sum(increase(kube_pod_container_status_restarts_total"
            f'{{namespace="{namespace}",pod=~"{service}-.*"}}[{lookback_minutes}m]))'
        )
        log_query = (
            f'{{namespace="{namespace}", service="{service}"}} '
            '|~ "(?i)error|exception|fatal"'
        )

        error_rate_result, latency_result, restart_result, log_result = await asyncio.gather(
            self.prometheus.query_range(error_rate_query, start, end, step),
            self.prometheus.query_range(latency_query, start, end, step),
            self.prometheus.query_range(restart_query, start, end, step),
            self.loki.query_range(log_query, start, end),
        )

        error_rate = _max_prometheus_value(error_rate_result)
        latency_p95_ms = _max_prometheus_value(latency_result)
        restart_count = _max_prometheus_value(restart_result)
        error_log_count = _count_loki_log_lines(log_result)

        anomalies: list[str] = []
        evidence = [
            f"Observed error rate {error_rate:.3f}.",
            f"Observed p95 latency {latency_p95_ms:.1f} ms.",
            f"Observed restart count {restart_count:.1f}.",
            f"Observed {error_log_count} error-like log lines.",
        ]
        if error_rate > error_rate_threshold:
            anomalies.append(
                f"Error rate {error_rate:.3f} exceeded threshold {error_rate_threshold:.3f}."
            )
        if latency_p95_ms > latency_threshold_ms:
            anomalies.append(
                f"p95 latency {latency_p95_ms:.1f} ms exceeded threshold "
                f"{latency_threshold_ms:.1f} ms."
            )
        if restart_count > restart_threshold:
            anomalies.append(
                f"Restart count {restart_count:.1f} exceeded threshold {restart_threshold:.1f}."
            )
        if error_log_count > error_log_threshold:
            anomalies.append(
                f"Error log count {error_log_count} exceeded threshold {error_log_threshold}."
            )

        confidence = 0.92 if anomalies else 0.97
        return MonitoringSnapshot(
            service=service,
            namespace=namespace,
            window_minutes=lookback_minutes,
            error_rate=error_rate,
            latency_p95_ms=latency_p95_ms,
            restart_count=restart_count,
            error_log_count=error_log_count,
            anomalies=anomalies,
            evidence=evidence,
            confidence=confidence,
        )

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        if event.type not in {EventType.HEALTH_CHECK_TRIGGERED, EventType.ALERT_TRIGGERED}:
            return SwarmEvent(
                type=EventType.TASK_FAILED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload={
                    "error": "unsupported_event_type",
                    "received_type": event.type.value,
                },
                parent_event_id=event.id,
            )

        service = event.payload.get("service")
        namespace = event.payload.get("namespace", self.settings.k8s_namespace)
        if not isinstance(service, str) or not service:
            return SwarmEvent(
                type=EventType.TASK_FAILED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload={"error": "missing_service"},
                parent_event_id=event.id,
            )
        if not isinstance(namespace, str) or not namespace:
            namespace = self.settings.k8s_namespace

        lookback_minutes = _coerce_positive_int(event.payload.get("lookback_minutes"), default=15)
        snapshot = await self.monitor_service(
            service=service,
            namespace=namespace,
            lookback_minutes=lookback_minutes,
            error_rate_threshold=_coerce_positive_float(
                event.payload.get("error_rate_threshold"),
                default=0.05,
            ),
            latency_threshold_ms=_coerce_positive_float(
                event.payload.get("latency_threshold_ms"),
                default=750.0,
            ),
            restart_threshold=_coerce_positive_float(
                event.payload.get("restart_threshold"),
                default=1.0,
            ),
            error_log_threshold=_coerce_positive_int(
                event.payload.get("error_log_threshold"),
                default=3,
            ),
        )
        response_type = (
            EventType.ANOMALY_DETECTED if snapshot.anomalies else EventType.TASK_COMPLETED
        )
        return SwarmEvent(
            type=response_type,
            task_id=event.task_id,
            source_agent=self.agent_name,
            target_agent=event.source_agent,
            payload=snapshot.model_dump(mode="json"),
            metadata={
                "confidence": snapshot.confidence,
                "anomaly_count": len(snapshot.anomalies),
            },
            parent_event_id=event.id,
        )

    async def health_check(self) -> dict[str, Any]:
        status = self.default_health_status()
        status["capabilities"] = ["prometheus_query", "loki_query", "anomaly_detection"]
        return status


def _coerce_positive_float(value: object, *, default: float) -> float:
    if isinstance(value, int | float) and float(value) >= 0:
        return float(value)
    return default


def _coerce_positive_int(value: object, *, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return default


def _max_prometheus_value(result: dict[str, object]) -> float:
    data = result.get("data")
    if not isinstance(data, dict):
        return 0.0
    raw_series = data.get("result", [])
    if not isinstance(raw_series, list):
        return 0.0

    maximum = 0.0
    for series in raw_series:
        if not isinstance(series, dict):
            continue
        values = series.get("values", [])
        if not isinstance(values, list):
            continue
        for sample in values:
            if not isinstance(sample, list | tuple) or len(sample) != 2:
                continue
            raw_value = sample[1]
            if isinstance(raw_value, str):
                try:
                    maximum = max(maximum, float(raw_value))
                except ValueError:
                    continue
    return maximum


def _count_loki_log_lines(result: dict[str, object]) -> int:
    data = result.get("data")
    if not isinstance(data, dict):
        return 0
    raw_streams = data.get("result", [])
    if not isinstance(raw_streams, list):
        return 0

    count = 0
    for stream in raw_streams:
        if not isinstance(stream, dict):
            continue
        values = stream.get("values", [])
        if isinstance(values, list):
            count += len(values)
    return count
