from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from forge.core.exceptions import InsufficientEvidenceError

MIN_HYPOTHESIS_CONFIDENCE = 0.70
"""Minimum confidence required before a hypothesis may leave the agent.

This is the FORGE hallucination hard stop: any LLM-produced root-cause that
falls below this threshold MUST be sent back for reinvestigation, not shown
to humans as a recommendation.
"""


class EvidenceItem(BaseModel):
    """Weighted evidence collected during incident investigation."""

    source: str = Field(description="Agent or subsystem that produced the evidence.")
    summary: str = Field(description="Human-readable evidence summary.")
    weight: float = Field(ge=0.0, le=1.0, description="Relative importance of this evidence.")


class RootCauseHypothesis(BaseModel):
    """Evidence-backed root-cause hypothesis produced during remediation."""

    summary: str = Field(description="Most likely root cause under investigation.")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)


def assert_hypothesis_is_grounded(hypothesis: RootCauseHypothesis) -> None:
    """Raise :class:`InsufficientEvidenceError` if the hypothesis fails the guard.

    A hypothesis is grounded only when:

    * at least one evidence item is attached;
    * its confidence is at or above :data:`MIN_HYPOTHESIS_CONFIDENCE`.

    Callers that want to surface a hypothesis to a human (Slack approval, web
    UI, audit log) MUST run this guard first. Hypotheses that fail must be
    rerouted into the reinvestigation loop.
    """

    if not hypothesis.evidence:
        raise InsufficientEvidenceError(
            "RootCauseHypothesis carries no evidence; refusing to surface "
            "unsupported diagnoses."
        )
    if hypothesis.confidence < MIN_HYPOTHESIS_CONFIDENCE:
        raise InsufficientEvidenceError(
            "RootCauseHypothesis confidence "
            f"{hypothesis.confidence:.2f} is below the FORGE guard threshold "
            f"{MIN_HYPOTHESIS_CONFIDENCE:.2f}; reinvestigate before recommending."
        )


class FixProposal(BaseModel):
    """Candidate remediation generated for an incident."""

    strategy: Literal["rollback", "config_change", "restart", "observe"] = Field(
        description="Remediation strategy selected for the incident.",
    )
    summary: str = Field(description="Short user-facing summary of the proposed fix.")
    change_plan: str = Field(description="Implementation plan or diff summary.")
    undo_path: str = Field(description="How the change can be reverted safely.")
    test_plan: str = Field(description="Sandbox or runtime validation plan.")
    requires_human_approval: bool = Field(description="Whether live execution must be approved.")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    deployment_name: str | None = Field(default=None)
    previous_revision: str | None = Field(default=None)


class FixEvaluation(BaseModel):
    """Safety and quality evaluation of a candidate remediation."""

    score: float = Field(ge=0.0, le=1.0)
    safe_for_sandbox: bool = Field(description="Whether sandbox validation may proceed.")
    requires_human_approval: bool = Field(description="Whether approval remains mandatory.")
    rationale: list[str] = Field(default_factory=list)


class FixEvaluator:
    """Score incident fixes before FORGE attempts sandbox or live execution."""

    def evaluate(self, proposal: FixProposal) -> FixEvaluation:
        score = proposal.confidence
        rationale = [f"Starting from proposal confidence {proposal.confidence:.2f}."]
        if proposal.undo_path.strip():
            score += 0.1
            rationale.append("Undo path is present.")
        if "test" in proposal.test_plan.lower() or "sandbox" in proposal.test_plan.lower():
            score += 0.1
            rationale.append("Validation plan includes sandbox or explicit tests.")
        if proposal.strategy == "observe":
            score -= 0.2
            rationale.append(
                "Observation-only plans are intentionally treated as lower-confidence."
            )
        if proposal.strategy == "rollback":
            score += 0.05
            rationale.append(
                "Rollback is preferred when recent changes correlate with the incident."
            )
        final_score = max(0.0, min(1.0, score))
        safe_for_sandbox = proposal.strategy in {"rollback", "config_change", "restart"}
        return FixEvaluation(
            score=final_score,
            safe_for_sandbox=safe_for_sandbox,
            requires_human_approval=proposal.requires_human_approval,
            rationale=rationale,
        )
