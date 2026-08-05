"""Unit tests for Monitoring Agent anomaly math."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from swarm.agents.monitoring import classify_severity, detect_anomaly
from swarm.schemas import Severity, SwarmConfig
from swarm.tools.prometheus import MetricSample


def _series(baseline: float, spike: float, *, n_base: int = 20, n_spike: int = 20):
    start = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)
    samples: list[MetricSample] = []
    for i in range(n_base):
        samples.append(
            MetricSample(timestamp=start + timedelta(seconds=30 * i), value=baseline)
        )
    spike_start = start + timedelta(seconds=30 * n_base)
    for i in range(n_spike):
        samples.append(
            MetricSample(timestamp=spike_start + timedelta(seconds=30 * i), value=spike)
        )
    return samples


def test_detects_clear_spike():
    config = SwarmConfig(z_threshold=3.0, min_spike_ratio=2.0, baseline_fraction=0.5)
    signal = detect_anomaly(
        _series(0.01, 0.40),
        config,
        service="checkout-api",
        metric_name="error ratio",
        query="test",
    )
    assert signal is not None
    assert signal.spike_magnitude >= 10
    assert signal.severity in {Severity.HIGH, Severity.CRITICAL}
    assert signal.start_timestamp is not None


def test_no_false_positive_on_flat_series():
    config = SwarmConfig(z_threshold=3.0, min_spike_ratio=2.0, baseline_fraction=0.5)
    signal = detect_anomaly(
        _series(0.01, 0.012),
        config,
        service="checkout-api",
        metric_name="error ratio",
        query="test",
    )
    assert signal is None


def test_severity_buckets():
    assert classify_severity(2.0) == Severity.LOW
    assert classify_severity(5.0) == Severity.MEDIUM
    assert classify_severity(12.0) == Severity.HIGH
    assert classify_severity(25.0) == Severity.CRITICAL
