"""Prometheus HTTP API client and the tools built on it.

Adapted from the previous ``watchman/prometheus_client.py``: same real call to
``/api/v1/query_range``, converted to a synchronous client and given typed
argument and result models so it can be registered as a swarm tool.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field

from swarm.tools.registry import Tool

DEFAULT_ERROR_RATIO_QUERY = (
    'sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[1m])) '
    '/ clamp_min(sum(rate(http_requests_total{{service="{service}"}}[1m])), 0.001)'
)


class MetricSample(BaseModel):
    """One (timestamp, value) point of a range query."""

    timestamp: datetime = Field(description="Sample time, UTC.")
    value: float = Field(description="Sample value.")


class MetricSeries(BaseModel):
    """One labelled series returned by a range query."""

    labels: dict[str, str] = Field(default_factory=dict, description="Series labels.")
    samples: list[MetricSample] = Field(
        default_factory=list, description="Points ordered by time."
    )


class QueryRangeArgs(BaseModel):
    """Arguments for ``prometheus.query_range``."""

    query: str = Field(description="PromQL expression to evaluate.")
    start: datetime = Field(description="Inclusive window start.")
    end: datetime = Field(description="Inclusive window end.")
    step_seconds: int = Field(default=30, ge=1, description="Resolution in seconds.")


class QueryRangeResult(BaseModel):
    """Result of ``prometheus.query_range``."""

    query: str = Field(description="Query that was executed.")
    series: list[MetricSeries] = Field(
        default_factory=list, description="Series returned by Prometheus."
    )
    endpoint: str = Field(description="Fully qualified URL that was called.")

    @property
    def flattened(self) -> list[MetricSample]:
        """All samples across all series, ordered by time."""

        samples = [sample for series in self.series for sample in series.samples]
        return sorted(samples, key=lambda item: item.timestamp)


class PrometheusClient:
    """Minimal synchronous Prometheus HTTP API client."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=self.base_url, timeout=timeout_seconds
        )

    def query_range(self, args: QueryRangeArgs) -> QueryRangeResult:
        """Run a range query against the real Prometheus HTTP API."""

        response = self._client.get(
            "/api/v1/query_range",
            params={
                "query": args.query,
                "start": args.start.timestamp(),
                "end": args.end.timestamp(),
                "step": f"{args.step_seconds}s",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise RuntimeError(f"prometheus returned status={payload.get('status')!r}")
        return QueryRangeResult(
            query=args.query,
            series=_parse_series(payload),
            endpoint=f"{self.base_url}/api/v1/query_range",
        )

    def is_ready(self) -> bool:
        """Whether Prometheus answers its readiness probe."""

        try:
            response = self._client.get("/-/ready")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def close(self) -> None:
        """Release the underlying connection pool when we own it."""

        if self._owns_client:
            self._client.close()


def _parse_series(payload: dict[str, object]) -> list[MetricSeries]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    raw_series = data.get("result")
    if not isinstance(raw_series, list):
        return []

    series: list[MetricSeries] = []
    for entry in raw_series:
        if not isinstance(entry, dict):
            continue
        labels = entry.get("metric", {})
        raw_values = entry.get("values", [])
        samples: list[MetricSample] = []
        if isinstance(raw_values, list):
            for point in raw_values:
                if not isinstance(point, list | tuple) or len(point) != 2:
                    continue
                try:
                    timestamp = datetime.fromtimestamp(float(point[0]), tz=UTC)
                    value = float(point[1])
                except (TypeError, ValueError):
                    continue
                if value != value:  # NaN, which Prometheus emits for empty ratios
                    continue
                samples.append(MetricSample(timestamp=timestamp, value=value))
        series.append(
            MetricSeries(
                labels={str(k): str(v) for k, v in labels.items()}
                if isinstance(labels, dict)
                else {},
                samples=sorted(samples, key=lambda item: item.timestamp),
            )
        )
    return series


def build_prometheus_tool(client: PrometheusClient) -> Tool:
    """Register the range query as a swarm tool."""

    return Tool(
        name="prometheus.query_range",
        description=(
            "Evaluate a PromQL expression over a time window against the Prometheus "
            "HTTP API and return the resulting time series."
        ),
        args_model=QueryRangeArgs,
        result_model=QueryRangeResult,
        handler=client.query_range,
    )
