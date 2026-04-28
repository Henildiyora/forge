"""Tests for the hallucination guard on root-cause hypotheses.

A hypothesis must carry evidence and meet the confidence threshold before it
can be turned into a fix proposal. These tests verify both the standalone
guard and the wiring inside :class:`RemediationAgent.propose_fix`.
"""

from __future__ import annotations

import pytest

from forge.agents.remediation.agent import RemediationAgent
from forge.agents.remediation.fix_evaluator import (
    MIN_HYPOTHESIS_CONFIDENCE,
    EvidenceItem,
    RootCauseHypothesis,
    assert_hypothesis_is_grounded,
)
from forge.cli.runtime import local_message_bus
from forge.core.config import Settings
from forge.core.exceptions import InsufficientEvidenceError


def _evidence(weight: float = 0.8) -> list[EvidenceItem]:
    return [
        EvidenceItem(source="watchman", summary="error rate 0.18", weight=weight),
        EvidenceItem(source="kubernetes", summary="2 restarts", weight=weight),
    ]


def test_guard_passes_when_grounded() -> None:
    hypothesis = RootCauseHypothesis(
        summary="rollout regression",
        confidence=0.85,
        evidence=_evidence(),
    )
    assert_hypothesis_is_grounded(hypothesis)


def test_guard_rejects_empty_evidence() -> None:
    hypothesis = RootCauseHypothesis(
        summary="rollout regression",
        confidence=0.95,
        evidence=[],
    )
    with pytest.raises(InsufficientEvidenceError, match="no evidence"):
        assert_hypothesis_is_grounded(hypothesis)


def test_guard_rejects_low_confidence() -> None:
    hypothesis = RootCauseHypothesis(
        summary="rollout regression",
        confidence=MIN_HYPOTHESIS_CONFIDENCE - 0.01,
        evidence=_evidence(),
    )
    with pytest.raises(InsufficientEvidenceError, match="below the FORGE guard"):
        assert_hypothesis_is_grounded(hypothesis)


@pytest.mark.asyncio
async def test_propose_fix_blocks_actionable_unsupported_hypotheses() -> None:
    settings = Settings(app_env="test", llm_backend="heuristic")
    bus = local_message_bus(settings)
    agent = RemediationAgent(settings=settings, message_bus=bus)

    weak_with_actionable_alert = RootCauseHypothesis(
        summary="vibes", confidence=0.55, evidence=_evidence()
    )
    actionable_alert = {
        "service": "demo",
        "recent_change_detected": True,
        "error_rate": 0.18,
    }
    with pytest.raises(InsufficientEvidenceError):
        await agent.propose_fix(alert_data=actionable_alert, hypothesis=weak_with_actionable_alert)

    no_evidence_with_high_confidence = RootCauseHypothesis(
        summary="something is off", confidence=0.95, evidence=[]
    )
    with pytest.raises(InsufficientEvidenceError):
        await agent.propose_fix(
            alert_data=actionable_alert,
            hypothesis=no_evidence_with_high_confidence,
        )


@pytest.mark.asyncio
async def test_propose_fix_allows_observe_for_low_confidence_signals() -> None:
    """Low-confidence hypotheses MAY result in an observe proposal (no action)."""

    settings = Settings(app_env="test", llm_backend="heuristic")
    bus = local_message_bus(settings)
    agent = RemediationAgent(settings=settings, message_bus=bus)
    weak = RootCauseHypothesis(summary="weak signal", confidence=0.55, evidence=_evidence())
    benign_alert = {
        "service": "demo",
        "error_rate": 0.0,
        "latency_p95_ms": 100.0,
        "restart_count": 0.0,
    }
    proposal = await agent.propose_fix(alert_data=benign_alert, hypothesis=weak)
    assert proposal.strategy == "observe"


@pytest.mark.asyncio
async def test_propose_fix_accepts_grounded_hypothesis() -> None:
    settings = Settings(app_env="test", llm_backend="heuristic")
    bus = local_message_bus(settings)
    agent = RemediationAgent(settings=settings, message_bus=bus)
    grounded = RootCauseHypothesis(
        summary="rollout regression",
        confidence=0.85,
        evidence=_evidence(),
    )
    proposal = await agent.propose_fix(
        alert_data={
            "service": "demo",
            "deployment_name": "demo",
            "previous_revision": "1",
            "error_rate": 0.18,
            "recent_change_detected": True,
        },
        hypothesis=grounded,
    )
    assert proposal.strategy in {"rollback", "config_change", "restart", "observe"}
    assert proposal.evidence
