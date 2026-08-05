"""Unit tests for Code Analysis relevance heuristic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from swarm.agents.code_analysis import score_commit
from swarm.schemas import IncidentSignal, Severity, SwarmConfig
from swarm.tools.github import CommitRecord


def _incident(ts: datetime) -> IncidentSignal:
    return IncidentSignal(
        service="checkout-api",
        metric_name="error ratio",
        query="q",
        start_timestamp=ts,
        baseline_value=0.01,
        peak_value=0.4,
        spike_magnitude=40.0,
        z_score=20.0,
        severity=Severity.CRITICAL,
        sample_count=40,
    )


def test_nearby_service_path_commit_ranks_highest():
    incident_ts = datetime(2026, 8, 4, 17, 35, tzinfo=UTC)
    config = SwarmConfig(
        service_paths=["sandbox/target_service/", "checkout/"],
        commit_window_before_minutes=60,
        commit_window_after_minutes=5,
    )
    culprit = CommitRecord(
        sha="a" * 40,
        author="alex",
        message="perf: lower PAYMENT_TIMEOUT_MS",
        timestamp=incident_ts - timedelta(minutes=8),
        files_changed=["sandbox/target_service/app.py", "checkout/config.yaml"],
    )
    decoy = CommitRecord(
        sha="b" * 40,
        author="bot",
        message="docs: update readme",
        timestamp=incident_ts - timedelta(minutes=40),
        files_changed=["README.md"],
    )
    post = CommitRecord(
        sha="c" * 40,
        author="oncall",
        message="chore: page oncall",
        timestamp=incident_ts + timedelta(minutes=3),
        files_changed=["docs/incident.md"],
    )
    scored = [score_commit(c, _incident(incident_ts), config) for c in (culprit, decoy, post)]
    scored.sort(key=lambda c: c.relevance_score, reverse=True)
    assert scored[0].sha == culprit.sha
    assert scored[0].relevance_score > scored[1].relevance_score
    assert any("service paths" in r for r in scored[0].relevance_reasons)
