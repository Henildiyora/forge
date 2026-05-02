from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from forge.agents.base import BaseAgent
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus
from forge.core.strategies import DeploymentStrategy
from forge.orchestrator.state import SwarmState

CaptainNextAction = Literal["retry_generation", "complete", "halt"]
IncidentNextAction = Literal["observe", "request_approval", "halt"]
IncidentSeverity = Literal["low", "medium", "high", "critical"]


class CaptainDecision(BaseModel):
    """Decision emitted by the Captain after reviewing workflow state."""

    next_action: CaptainNextAction = Field(
        description="Next action the orchestrator should take."
    )
    reason: str = Field(description="Human-readable explanation for the decision.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Captain confidence in the chosen action.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting the decision.",
    )


class IncidentResponseDecision(BaseModel):
    """Decision emitted by the Captain after incident triage."""

    next_action: IncidentNextAction = Field(description="Next action for the incident workflow.")
    severity: IncidentSeverity = Field(description="Captain-assigned incident severity.")
    reason: str = Field(description="Human-readable explanation for the decision.")
    proposed_action: str = Field(description="Recommended next action or remediation path.")
    root_cause_hypothesis: str = Field(description="Best-supported incident hypothesis so far.")
    requires_human_approval: bool = Field(
        description="Whether the proposed action must be approved by a human.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting the incident decision.",
    )


class CaptainAgent(BaseAgent):
    """User-facing orchestration agent for high-level workflow decisions."""

    agent_name = "captain"

    def __init__(self, settings: Settings, message_bus: MessageBus) -> None:
        super().__init__(settings, message_bus)

    def review_deployment_state(self, state: SwarmState) -> CaptainDecision:
        """Review deploy workflow state and choose the next action."""

        forge_strategy = _forge_strategy_from_metadata(state)
        config_agents = _config_agents_for_strategy(forge_strategy)
        config_attempts = state.step_iterations.get("config_generation", 0)
        failed_agents = [
            agent_name
            for agent_name in config_agents
            if agent_name in state.agent_results and not state.agent_results[agent_name].success
        ]
        if failed_agents:
            if config_attempts < state.max_iterations:
                return CaptainDecision(
                    next_action="retry_generation",
                    reason="At least one config generation agent failed.",
                    confidence=0.45,
                    evidence=[f"Failed agents: {', '.join(failed_agents)}."],
                )
            return CaptainDecision(
                next_action="halt",
                reason="Config generation kept failing beyond the retry budget.",
                confidence=0.9,
                evidence=[
                    f"Failed agents after {config_attempts} attempts: "
                    f"{', '.join(failed_agents)}."
                ],
            )

        missing_artifacts = _missing_artifacts_for_strategy(state, forge_strategy)
        if missing_artifacts:
            if config_attempts < state.max_iterations:
                return CaptainDecision(
                    next_action="retry_generation",
                    reason="Required deployment artifacts are still missing.",
                    confidence=0.4,
                    evidence=[
                        f"Missing artifacts: {', '.join(missing_artifacts)}.",
                    ],
                )
            return CaptainDecision(
                next_action="halt",
                reason="Required deployment artifacts are missing after repeated attempts.",
                confidence=0.85,
                evidence=[
                    f"Missing artifacts after {config_attempts} attempts: "
                    f"{', '.join(missing_artifacts)}."
                ],
            )

        alignment_issues = self._deployment_alignment_issues(state, forge_strategy=forge_strategy)
        if alignment_issues:
            if config_attempts < state.max_iterations:
                return CaptainDecision(
                    next_action="retry_generation",
                    reason="Generated deployment artifacts are inconsistent with the scan.",
                    confidence=0.55,
                    evidence=alignment_issues,
                )
            return CaptainDecision(
                next_action="halt",
                reason="Generated deployment artifacts stayed inconsistent after retries.",
                confidence=0.85,
                evidence=alignment_issues,
            )

        confidences = [
            state.agent_results[agent_name].confidence
            for agent_name in config_agents
            if agent_name in state.agent_results and state.agent_results[agent_name].success
        ]
        minimum_confidence = min(confidences) if confidences else 0.0
        if minimum_confidence < 0.7:
            if config_attempts < state.max_iterations:
                return CaptainDecision(
                    next_action="retry_generation",
                    reason="Deployment plan confidence is below the acceptance threshold.",
                    confidence=minimum_confidence,
                    evidence=[
                        f"Lowest specialist confidence is {minimum_confidence:.2f}; "
                        "threshold is 0.70."
                    ],
                )
            return CaptainDecision(
                next_action="halt",
                reason="Deployment plan confidence stayed too low after the retry budget.",
                confidence=minimum_confidence,
                evidence=[
                    f"Lowest specialist confidence remained {minimum_confidence:.2f} "
                    f"after {config_attempts} attempts."
                ],
            )

        framework = str(state.project_metadata.get("framework", "unknown"))
        return CaptainDecision(
            next_action="complete",
            reason=f"Deployment plan is ready for the detected {framework} project.",
            confidence=minimum_confidence if confidences else 0.95,
            evidence=[
                f"All config agents succeeded with minimum confidence {minimum_confidence:.2f}.",
                f"Artifacts present: dockerfile={state.dockerfile is not None}, "
                f"k8s_manifests={bool(state.k8s_manifests)}, "
                f"cicd_pipeline={state.cicd_pipeline is not None}.",
            ],
        )

    def review_incident_state(self, state: SwarmState) -> IncidentResponseDecision:
        """Review an anomaly or alert and decide how the incident should proceed."""

        if not state.alert_data:
            return IncidentResponseDecision(
                next_action="halt",
                severity="high",
                reason="Incident workflow cannot continue without alert data.",
                proposed_action=(
                    "Collect a Watchman anomaly payload before retrying incident triage."
                ),
                root_cause_hypothesis="Insufficient incident context was provided.",
                requires_human_approval=False,
                confidence=0.15,
                evidence=["alert_data is empty."],
            )

        service = str(state.alert_data.get("service", "unknown"))
        anomalies = state.alert_data.get("anomalies", [])
        anomaly_count = len(anomalies) if isinstance(anomalies, list) else 0
        error_rate = _coerce_float(state.alert_data.get("error_rate"))
        latency_p95_ms = _coerce_float(state.alert_data.get("latency_p95_ms"))
        restart_count = _coerce_float(state.alert_data.get("restart_count"))
        error_log_count = _coerce_int(state.alert_data.get("error_log_count"))

        severity = _incident_severity(
            anomaly_count=anomaly_count,
            error_rate=error_rate,
            latency_p95_ms=latency_p95_ms,
            restart_count=restart_count,
            error_log_count=error_log_count,
        )
        root_cause_hypothesis = _incident_hypothesis(
            service=service,
            error_rate=error_rate,
            latency_p95_ms=latency_p95_ms,
            restart_count=restart_count,
            error_log_count=error_log_count,
        )
        evidence = [
            f"Service under investigation: {service}.",
            f"Detected anomaly count: {anomaly_count}.",
            f"Observed error rate {error_rate:.3f}, p95 latency {latency_p95_ms:.1f} ms, "
            f"restart count {restart_count:.1f}, error log count {error_log_count}.",
        ]
        sandbox_passed = state.sandbox_test_passed
        if not sandbox_passed:
            evidence.append("No successful sandbox validation is recorded for the proposed change.")

        requires_human_approval = severity in {"high", "critical"} or not sandbox_passed
        if severity == "critical":
            proposed_action = (
                f"Freeze changes for {service}, prepare rollback or containment steps, and seek "
                "human approval before executing remediation."
            )
            reason = "Incident severity is critical and requires immediate human review."
        elif severity == "high":
            proposed_action = (
                f"Prepare a rollback or targeted mitigation plan for {service} and request "
                "human approval before any live action."
            )
            reason = "Incident severity is high enough to require a human approval gate."
        elif requires_human_approval:
            proposed_action = (
                f"Collect additional runtime evidence for {service} and request human approval "
                "before changing live infrastructure."
            )
            reason = "Live action remains gated because sandbox validation has not passed."
        else:
            proposed_action = (
                f"Continue observing {service}, capture additional telemetry, and avoid live "
                "changes unless the signal worsens."
            )
            reason = "Incident can remain in observation mode without immediate human approval."

        next_action: IncidentNextAction = (
            "request_approval" if requires_human_approval else "observe"
        )
        confidence = 0.93 if requires_human_approval else 0.81
        return IncidentResponseDecision(
            next_action=next_action,
            severity=severity,
            reason=reason,
            proposed_action=proposed_action,
            root_cause_hypothesis=root_cause_hypothesis,
            requires_human_approval=requires_human_approval,
            confidence=confidence,
            evidence=evidence,
        )

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        if event.type == EventType.CODEBASE_SCAN_COMPLETED:
            return SwarmEvent(
                type=EventType.DEPLOYMENT_PLAN_REQUESTED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=None,
                payload={"scan_result": event.payload},
                metadata={"requested_by": self.agent_name},
                parent_event_id=event.id,
            )
        if event.type == EventType.ANOMALY_DETECTED:
            return SwarmEvent(
                type=EventType.INVESTIGATION_REQUESTED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=self.agent_name,
                payload={"alert_data": event.payload},
                metadata={"requested_by": self.agent_name},
                parent_event_id=event.id,
            )
        if event.type == EventType.TASK_FAILED:
            return SwarmEvent(
                type=EventType.TASK_FAILED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload=event.payload,
                parent_event_id=event.id,
            )
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

    async def health_check(self) -> dict[str, object]:
        status = self.default_health_status()
        status["capabilities"] = ["deploy_review", "workflow_routing", "artifact_reconciliation"]
        return status

    def _deployment_alignment_issues(
        self,
        state: SwarmState,
        *,
        forge_strategy: DeploymentStrategy | None = None,
    ) -> list[str]:
        issues: list[str] = []
        if forge_strategy == DeploymentStrategy.CICD_ONLY:
            return issues

        expected_port = state.project_metadata.get("port")
        if isinstance(expected_port, int):
            docker_port = _extract_docker_port(state.dockerfile)
            if docker_port is not None and docker_port != expected_port:
                issues.append(
                    f"Dockerfile exposes port {docker_port}, but the scan detected port "
                    f"{expected_port}."
                )

            compose_port = _extract_compose_port(state.docker_compose)
            if compose_port is not None and compose_port != expected_port:
                issues.append(
                    f"docker-compose publishes port {compose_port}, but the scan detected port "
                    f"{expected_port}."
                )

            if forge_strategy != DeploymentStrategy.DOCKER_COMPOSE:
                for manifest_name, manifest in state.k8s_manifests.items():
                    manifest_port = _extract_manifest_port(manifest)
                    if manifest_port is not None and manifest_port != expected_port:
                        issues.append(
                            f"{manifest_name} references port {manifest_port}, but the scan "
                            f"detected port {expected_port}."
                        )

        env_vars = state.project_metadata.get("env_vars")
        if isinstance(env_vars, list):
            expected_env_vars = [env_var for env_var in env_vars if isinstance(env_var, str)]
            compose_text = state.docker_compose or ""
            missing_in_compose = [
                env_var for env_var in expected_env_vars if env_var not in compose_text
            ]
            if missing_in_compose and forge_strategy in (
                None,
                DeploymentStrategy.DOCKER_COMPOSE,
                DeploymentStrategy.KUBERNETES,
            ):
                issues.append(
                    "docker-compose is missing environment variables required by the scan: "
                    f"{', '.join(missing_in_compose)}."
                )
            if forge_strategy not in (DeploymentStrategy.DOCKER_COMPOSE, DeploymentStrategy.CICD_ONLY):
                manifest_text = "\n".join(state.k8s_manifests.values())
                missing_in_manifests = [
                    env_var for env_var in expected_env_vars if env_var not in manifest_text
                ]
                if missing_in_manifests:
                    issues.append(
                        "Kubernetes manifests are missing environment variables required by the "
                        f"scan: {', '.join(missing_in_manifests)}."
                    )
        return issues


def _forge_strategy_from_metadata(state: SwarmState) -> DeploymentStrategy | None:
    raw = state.project_metadata.get("forge_strategy")
    if raw is None:
        return None
    try:
        return DeploymentStrategy(str(raw))
    except ValueError:
        return None


def _config_agents_for_strategy(strategy: DeploymentStrategy | None) -> tuple[str, ...]:
    """Agents whose results Captain must validate for the selected build strategy."""

    if strategy is None:
        return ("docker_specialist", "k8s_specialist", "cicd_specialist")
    if strategy == DeploymentStrategy.DOCKER_COMPOSE:
        return ("docker_specialist",)
    if strategy == DeploymentStrategy.CICD_ONLY:
        return ("cicd_specialist",)
    if strategy == DeploymentStrategy.KUBERNETES:
        return ("docker_specialist", "k8s_specialist", "cicd_specialist")
    return ("docker_specialist", "k8s_specialist", "cicd_specialist")


def _missing_artifacts_for_strategy(
    state: SwarmState,
    strategy: DeploymentStrategy | None,
) -> list[str]:
    missing: list[str] = []
    if strategy is None or strategy == DeploymentStrategy.KUBERNETES:
        if state.dockerfile is None:
            missing.append("dockerfile")
        if not state.k8s_manifests:
            missing.append("k8s_manifests")
        if state.cicd_pipeline is None:
            missing.append("cicd_pipeline")
    elif strategy == DeploymentStrategy.DOCKER_COMPOSE:
        if state.dockerfile is None:
            missing.append("dockerfile")
    elif strategy == DeploymentStrategy.CICD_ONLY:
        if state.cicd_pipeline is None:
            missing.append("cicd_pipeline")
    else:
        if state.dockerfile is None:
            missing.append("dockerfile")
        if not state.k8s_manifests:
            missing.append("k8s_manifests")
        if state.cicd_pipeline is None:
            missing.append("cicd_pipeline")
    return missing


def _extract_docker_port(dockerfile: str | None) -> int | None:
    if dockerfile is None:
        return None
    match = re.search(r"^EXPOSE\s+(\d+)", dockerfile, flags=re.MULTILINE)
    if match is None:
        return None
    return int(match.group(1))


def _extract_compose_port(compose_content: str | None) -> int | None:
    if compose_content is None:
        return None
    match = re.search(r"(\d+):(\d+)", compose_content)
    if match is None:
        return None
    return int(match.group(2))


def _extract_manifest_port(manifest: str) -> int | None:
    for pattern in (r"containerPort:\s*(\d+)", r"targetPort:\s*(\d+)", r"port:\s*(\d+)"):
        match = re.search(pattern, manifest)
        if match is not None:
            return int(match.group(1))
    return None


def _coerce_float(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _coerce_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _incident_severity(
    *,
    anomaly_count: int,
    error_rate: float,
    latency_p95_ms: float,
    restart_count: float,
    error_log_count: int,
) -> IncidentSeverity:
    if (
        anomaly_count >= 3
        or error_rate >= 0.15
        or latency_p95_ms >= 1500
        or restart_count >= 3
        or error_log_count >= 8
    ):
        return "critical"
    if (
        anomaly_count >= 2
        or error_rate >= 0.05
        or latency_p95_ms >= 750
        or restart_count >= 1
        or error_log_count >= 3
    ):
        return "high"
    if anomaly_count >= 1 or error_log_count >= 1:
        return "medium"
    return "low"


def _incident_hypothesis(
    *,
    service: str,
    error_rate: float,
    latency_p95_ms: float,
    restart_count: float,
    error_log_count: int,
) -> str:
    if restart_count >= 1:
        return (
            f"Service {service} is unstable at runtime, likely due to crashing pods or a bad "
            "recent rollout."
        )
    if error_rate >= 0.05 and latency_p95_ms >= 750:
        return (
            f"Service {service} is experiencing a degraded dependency or resource bottleneck "
            "that is driving both errors and latency."
        )
    if error_log_count >= 1:
        return f"Service {service} is emitting application-level errors that need investigation."
    return f"Service {service} shows a low-confidence anomaly that should remain under observation."
