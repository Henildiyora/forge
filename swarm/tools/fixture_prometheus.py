"""Replay a seeded OpenMetrics file as if it were Prometheus query_range.

Used by unit tests and by ``benchmark --offline``. The live path still talks to
a real Prometheus over HTTP; this exists so the anomaly math and graph routing
can be exercised without Docker.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from swarm.tools.prometheus import (
    MetricSample,
    MetricSeries,
    QueryRangeArgs,
    QueryRangeResult,
)

_LINE = re.compile(
    r'^http_requests_total\{([^}]*)\}\s+([0-9.eE+-]+)\s+(\d+)\s*$'
)


def _parse_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        labels[key.strip()] = value.strip().strip('"')
    return labels


def load_counter_series(path: Path) -> dict[str, list[tuple[datetime, float]]]:
    """Return ``{status: [(ts, value), ...]}`` for http_requests_total."""

    series: dict[str, list[tuple[datetime, float]]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _LINE.match(line.strip())
        if not match:
            continue
        labels = _parse_labels(match.group(1))
        status = labels.get("status", "unknown")
        value = float(match.group(2))
        # Fixtures use unix seconds (promtool v2.53 OpenMetrics convention).
        raw_ts = int(match.group(3))
        # Defend against older millisecond fixtures if present.
        ts = (
            datetime.fromtimestamp(raw_ts / 1000.0, tz=UTC)
            if raw_ts > 10_000_000_000
            else datetime.fromtimestamp(raw_ts, tz=UTC)
        )
        series.setdefault(status, []).append((ts, value))
    for status in series:
        series[status].sort(key=lambda item: item[0])
    return series


def error_ratio_samples(
    path: Path,
    *,
    start: datetime,
    end: datetime,
    step_seconds: int,
) -> list[MetricSample]:
    """Derive an error-ratio series from counter fixtures over ``[start, end]``."""

    counters = load_counter_series(path)
    ok = counters.get("2xx", [])
    err = counters.get("5xx", [])
    if not ok or not err:
        return []

    # Align by timestamp.
    ok_map = {ts: value for ts, value in ok}
    err_map = {ts: value for ts, value in err}
    timestamps = sorted(set(ok_map) & set(err_map))
    timestamps = [ts for ts in timestamps if start <= ts <= end]
    if len(timestamps) < 2:
        return []

    samples: list[MetricSample] = []
    for prev, curr in zip(timestamps, timestamps[1:], strict=False):
        dt = (curr - prev).total_seconds()
        if dt <= 0:
            continue
        dok = ok_map[curr] - ok_map[prev]
        derr = err_map[curr] - err_map[prev]
        total = dok + derr
        ratio = (derr / total) if total > 0 else 0.0
        # Emit at the end of each step window, matching rate()-like behavior.
        if (curr - start).total_seconds() % step_seconds <= step_seconds:
            samples.append(MetricSample(timestamp=curr, value=ratio))
    return samples


class FixturePrometheusClient:
    """Drop-in stand-in for PrometheusClient backed by an OpenMetrics file."""

    def __init__(self, fixture_path: Path) -> None:
        self.fixture_path = Path(fixture_path)
        self.base_url = f"fixture://{self.fixture_path}"

    def query_range(self, args: QueryRangeArgs) -> QueryRangeResult:
        samples = error_ratio_samples(
            self.fixture_path,
            start=args.start,
            end=args.end,
            step_seconds=args.step_seconds,
        )
        return QueryRangeResult(
            query=args.query,
            series=[MetricSeries(labels={"job": "fixture"}, samples=samples)],
            endpoint=f"{self.base_url}/api/v1/query_range",
        )

    def is_ready(self) -> bool:
        return self.fixture_path.exists()

    def close(self) -> None:
        return None
