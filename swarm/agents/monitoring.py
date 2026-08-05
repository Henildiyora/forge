"""Monitoring Agent: read Prometheus, decide whether an incident is happening.

Detection is a rolling-window z-score against a leading baseline. It is
deliberately simple and fully explainable: given the same series it always
produces the same verdict, and every number it reports can be recomputed by
hand from the raw samples.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta

from swarm.schemas import IncidentSignal, Severity, SwarmConfig, SwarmState, ToolCall, ToolResult
from swarm.tools.prometheus import (
    DEFAULT_ERROR_RATIO_QUERY,
    MetricSample,
    QueryRangeArgs,
    QueryRangeResult,
)
from swarm.tools.registry import ToolRegistry

AGENT_NAME = "monitoring_agent"

# Guards so a flat or near-zero baseline cannot produce an infinite z-score.
_SIGMA_ABSOLUTE_FLOOR = 1e-4
_SIGMA_RELATIVE_FLOOR = 0.05
_BASELINE_FLOOR = 1e-4


class AnomalyVerdict:
    """Namespace for severity bucket boundaries, expressed as spike ratios."""

    CRITICAL = 20.0
    HIGH = 10.0
    MEDIUM = 4.0


def build_query(config: SwarmConfig) -> str:
    """Return the PromQL to evaluate, defaulting to the 5xx error ratio."""

    if config.metric_query:
        return config.metric_query
    return DEFAULT_ERROR_RATIO_QUERY.format(service=config.service)


def classify_severity(spike_magnitude: float) -> Severity:
    """Bucket a spike ratio into a severity."""

    if spike_magnitude >= AnomalyVerdict.CRITICAL:
        return Severity.CRITICAL
    if spike_magnitude >= AnomalyVerdict.HIGH:
        return Severity.HIGH
    if spike_magnitude >= AnomalyVerdict.MEDIUM:
        return Severity.MEDIUM
    return Severity.LOW


def detect_anomaly(
    samples: list[MetricSample],
    config: SwarmConfig,
    *,
    service: str,
    metric_name: str,
    query: str,
) -> IncidentSignal | None:
    """Find the first sample that breaks out of the baseline distribution.

    The leading ``baseline_fraction`` of the series defines the normal
    distribution. A sample counts as anomalous when it clears both a z-score
    threshold and a ratio threshold; requiring both stops a statistically
    significant but practically meaningless wobble from paging anyone.
    """

    ordered = sorted(samples, key=lambda item: item.timestamp)
    if len(ordered) < 4:
        return None

    split = max(2, int(len(ordered) * config.baseline_fraction))
    if split >= len(ordered):
        return None

    baseline = ordered[:split]
    window = ordered[split:]
    baseline_values = [sample.value for sample in baseline]

    mean = statistics.fmean(baseline_values)
    stdev = statistics.pstdev(baseline_values)
    sigma = max(stdev, mean * _SIGMA_RELATIVE_FLOOR, _SIGMA_ABSOLUTE_FLOOR)
    denominator = max(mean, _BASELINE_FLOOR)

    breach: MetricSample | None = None
    for sample in window:
        z_score = (sample.value - mean) / sigma
        ratio = sample.value / denominator
        if z_score >= config.z_threshold and ratio >= config.min_spike_ratio:
            breach = sample
            break

    if breach is None:
        return None

    peak = max(window, key=lambda item: item.value)
    spike_magnitude = peak.value / denominator
    peak_z = (peak.value - mean) / sigma

    return IncidentSignal(
        service=service,
        metric_name=metric_name,
        query=query,
        start_timestamp=breach.timestamp,
        baseline_value=mean,
        peak_value=peak.value,
        spike_magnitude=spike_magnitude,
        z_score=peak_z,
        severity=classify_severity(spike_magnitude),
        sample_count=len(ordered),
        evidence=[
            f"Baseline mean over {len(baseline)} samples was {mean:.5f} "
            f"(stdev {stdev:.5f}, sigma floor applied: {sigma:.5f}).",
            f"First breach at {breach.timestamp.isoformat()} with value {breach.value:.5f} "
            f"(z={(breach.value - mean) / sigma:.1f}, "
            f"{breach.value / denominator:.1f}x baseline).",
            f"Peak value {peak.value:.5f} at {peak.timestamp.isoformat()} "
            f"is {spike_magnitude:.1f}x baseline (z={peak_z:.1f}).",
            f"Thresholds: z >= {config.z_threshold}, ratio >= {config.min_spike_ratio}.",
        ],
    )


class MonitoringAgent:
    """Queries Prometheus and writes an :class:`IncidentSignal` into shared state."""

    name = AGENT_NAME

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def run(
        self, state: SwarmState, *, now: datetime | None = None
    ) -> tuple[IncidentSignal | None, list[ToolCall], list[ToolResult]]:
        """Execute the agent, returning its output plus its tool audit trail."""

        config = state.config
        end = now or datetime.now(UTC)
        start = end - timedelta(minutes=config.lookback_minutes)
        query = build_query(config)

        call, result = self.registry.call(
            tool="prometheus.query_range",
            agent=self.name,
            arguments=QueryRangeArgs(
                query=query,
                start=start,
                end=end,
                step_seconds=config.step_seconds,
            ),
        )
        if not result.ok:
            raise RuntimeError(f"prometheus query failed: {result.error}")

        payload = QueryRangeResult.model_validate(result.payload)
        signal = detect_anomaly(
            payload.flattened,
            config,
            service=config.service,
            metric_name="http 5xx error ratio",
            query=query,
        )
        return signal, [call], [result]
