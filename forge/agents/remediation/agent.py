from __future__ import annotations

from typing import Literal

from forge.agents.base import BaseAgent
from forge.agents.remediation.fix_evaluator import (
    EvidenceItem,
    FixEvaluation,
    FixEvaluator,
    FixProposal,
    RootCauseHypothesis,
    assert_hypothesis_is_grounded,
)
from forge.core.checkpoints import CheckpointStore
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus


class RemediationAgent(BaseAgent):
    """Coordinator for Phase 2 incident investigation and fix planning."""

    agent_name = "remediation"

    def __init__(
        self,
        settings: Settings,
        message_bus: MessageBus,
        evaluator: FixEvaluator | None = None,
    ) -> None:
        super().__init__(settings, message_bus)
        self.evaluator = evaluator or FixEvaluator()

    async def collect_evidence(
        self,
        *,
        alert_data: dict[str, object],
    ) -> list[EvidenceItem]:
        service = str(alert_data.get("service", "unknown"))
        error_rate = _coerce_float(alert_data.get("error_rate"))
        latency_p95_ms = _coerce_float(alert_data.get("latency_p95_ms"))
        restart_count = _coerce_float(alert_data.get("restart_count"))
        error_log_count = _coerce_int(alert_data.get("error_log_count"))
        anomalies = alert_data.get("anomalies", [])
        anomaly_count = len(anomalies) if isinstance(anomalies, list) else 0
        evidence = [
            EvidenceItem(
                source="watchman",
                summary=f"{service} error rate is {error_rate:.3f}.",
                weight=0.92 if error_rate >= 0.05 else 0.45,
            ),
            EvidenceItem(
                source="watchman",
                summary=f"{service} p95 latency is {latency_p95_ms:.1f} ms.",
                weight=0.8 if latency_p95_ms >= 750.0 else 0.4,
            ),
            EvidenceItem(
                source="kubernetes",
                summary=f"{service} restart count is {restart_count:.1f}.",
                weight=0.78 if restart_count >= 1.0 else 0.35,
            ),
            EvidenceItem(
                source="logs",
                summary=f"{service} emitted {error_log_count} error-like log lines.",
                weight=0.74 if error_log_count >= 3 else 0.3,
            ),
            EvidenceItem(
                source="captain",
                summary=f"Detected anomaly count: {anomaly_count}.",
                weight=0.65 if anomaly_count >= 2 else 0.4,
            ),
        ]
        return evidence

    async def hypothesize_root_cause(
        self,
        *,
        alert_data: dict[str, object],
        evidence: list[EvidenceItem],
    ) -> RootCauseHypothesis:
        recent_change = bool(alert_data.get("recent_change_detected"))
        error_rate = _coerce_float(alert_data.get("error_rate"))
        restart_count = _coerce_float(alert_data.get("restart_count"))
        latency_p95_ms = _coerce_float(alert_data.get("latency_p95_ms"))
        if recent_change and error_rate >= 0.05:
            summary = "A recent rollout likely introduced a regression."
            confidence = 0.84
        elif restart_count >= 1.0:
            summary = "Runtime instability suggests a container or configuration failure."
            confidence = 0.8
        elif latency_p95_ms >= 750.0:
            summary = "The service is degraded under load and likely needs configuration tuning."
            confidence = 0.74
        else:
            summary = "The incident signal is weak and needs more investigation."
            confidence = 0.58
        return RootCauseHypothesis(summary=summary, confidence=confidence, evidence=evidence)

    async def propose_fix(
        self,
        *,
        alert_data: dict[str, object],
        hypothesis: RootCauseHypothesis,
    ) -> FixProposal:
        service = str(alert_data.get("service", "unknown"))
        namespace = str(alert_data.get("namespace", self.settings.k8s_namespace))
        recent_change = bool(alert_data.get("recent_change_detected"))
        error_rate = _coerce_float(alert_data.get("error_rate"))
        deployment_name = str(alert_data.get("deployment_name", service or "app"))
        previous_revision = str(alert_data.get("previous_revision", "1"))
        if recent_change or error_rate >= 0.10:
            assert_hypothesis_is_grounded(hypothesis)
            return FixProposal(
                strategy="rollback",
                summary=f"Rollback the latest {service} deployment in {namespace}.",
                change_plan=(
                    f"Run a controlled Kubernetes rollback for deployment/{deployment_name} "
                    "after sandbox and approval checks."
                ),
                undo_path=(
                    "Redeploy the current revision after validating the offending "
                    "change in sandbox."
                ),
                test_plan="Validate the suspected fix path in sandbox and watch live error rate.",
                requires_human_approval=True,
                confidence=max(0.75, hypothesis.confidence),
                evidence=hypothesis.evidence,
                deployment_name=deployment_name,
                previous_revision=previous_revision,
            )
        if hypothesis.confidence >= 0.70:
            assert_hypothesis_is_grounded(hypothesis)
            return FixProposal(
                strategy="config_change",
                summary=f"Adjust runtime configuration for {service}.",
                change_plan=(
                    "Prefer a small configuration-only change before touching application code."
                ),
                undo_path=(
                    "Revert the configuration overlay or redeploy the prior "
                    "ConfigMap/Secret."
                ),
                test_plan="Apply the configuration change in sandbox and run smoke tests.",
                requires_human_approval=True,
                confidence=hypothesis.confidence,
                evidence=hypothesis.evidence,
                deployment_name=deployment_name,
                previous_revision=previous_revision,
            )
        return FixProposal(
            strategy="observe",
            summary=f"Keep observing {service} while collecting more evidence.",
            change_plan="Do not apply live remediation yet; reinvestigate first.",
            undo_path="No undo path needed because no change will be applied.",
            test_plan="Collect another monitoring window before escalating.",
            requires_human_approval=False,
            confidence=hypothesis.confidence,
            evidence=hypothesis.evidence,
            deployment_name=deployment_name,
            previous_revision=previous_revision,
        )

    async def evaluate_fix(self, proposal: FixProposal) -> FixEvaluation:
        return self.evaluator.evaluate(proposal)

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        return SwarmEvent(
            type=EventType.TASK_COMPLETED,
            task_id=event.task_id,
            source_agent=self.agent_name,
            target_agent=event.source_agent,
            payload={"status": "remediation_planning_only"},
            parent_event_id=event.id,
        )

    async def health_check(self) -> dict[str, object]:
        status = self.default_health_status()
        status["capabilities"] = ["incident_investigation", "fix_planning", "fix_evaluation"]
        return status


async def resume_incident_remediation(
    *,
    settings: Settings,
    checkpoint_store: CheckpointStore,
    task_id: str,
    approved_by: str,
) -> FixProposal | None:
    """Load a paused incident checkpoint and return the approved remediation plan."""

    del settings, approved_by
    checkpoint = await checkpoint_store.load(task_id)
    if checkpoint is None or checkpoint.workflow_type != "incident":
        return None
    raw_fix = checkpoint.state.get("fix_proposal")
    if not isinstance(raw_fix, dict):
        await checkpoint_store.delete(task_id)
        return None
    return FixProposal.model_validate(raw_fix)


def remediation_event_type_from_action(
    action: Literal["approve", "reject", "reinvestigate"],
) -> str:
    if action == "approve":
        return "incident.remediation_approved"
    if action == "reject":
        return "incident.remediation_rejected"
    return "incident.reinvestigation_requested"


def _coerce_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0
