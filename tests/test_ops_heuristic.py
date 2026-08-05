"""Ops Agent deterministic planner."""

from __future__ import annotations

from datetime import UTC, datetime

from swarm.agents.ops import heuristic_propose
from swarm.schemas import CommitCandidate, IncidentSignal, Severity


def test_restores_broken_payment_timeout():
    incident = IncidentSignal(
        service="checkout-api",
        metric_name="error ratio",
        query="q",
        start_timestamp=datetime(2026, 8, 4, 17, 35, tzinfo=UTC),
        baseline_value=0.01,
        peak_value=0.4,
        spike_magnitude=40.0,
        z_score=20.0,
        severity=Severity.CRITICAL,
        sample_count=40,
        evidence=["PAYMENT_TIMEOUT_MS looks wrong"],
    )
    candidates = [
        CommitCandidate(
            sha="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            author="alex",
            message="perf: lower PAYMENT_TIMEOUT_MS to reduce tail latency",
            files_changed=["sandbox/target_service/app.py"],
            timestamp=datetime(2026, 8, 4, 17, 27, tzinfo=UTC),
            relevance_score=0.9,
            relevance_reasons=["nearby"],
            minutes_before_incident=8.0,
        )
    ]
    fix = heuristic_propose(
        incident,
        candidates,
        {"PAYMENT_TIMEOUT_MS": "50", "FEATURE_CHECKOUT_V2": "false", "MAX_RETRIES": "3"},
    )
    assert fix.source == "heuristic"
    targets = {a.target: a.value for a in fix.actions}
    assert targets.get("PAYMENT_TIMEOUT_MS") == "2000"
