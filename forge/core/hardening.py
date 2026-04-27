from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from pydantic import BaseModel, Field

from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.core.approvals import ApprovalRequest, approval_store
from forge.core.config import Settings
from forge.core.exceptions import InsufficientEvidenceError
from forge.core.llm import LLMClient, LLMResponse
from forge.core.message_bus import MessageBus, SupportsRedisStreams
from forge.core.observability import WorkflowObservabilitySummary, observability_store
from forge.orchestrator.graph import build_swarm_graph
from forge.orchestrator.state import SwarmState
from forge.orchestrator.workflows.deploy_workflow import (
    CICDGenerationResult,
    DeployWorkflowDependencies,
    DockerGenerationResult,
    KubernetesGenerationResult,
    build_default_deploy_dependencies,
)


class HardeningScenarioResult(BaseModel):
    """Outcome for one hardening or fault-injection scenario."""

    name: str = Field(description="Stable scenario name.")
    description: str = Field(description="What the scenario attempts to verify.")
    passed: bool = Field(description="Whether the system behaved as expected.")
    expected_outcome: str = Field(description="Safe behavior expected from the platform.")
    observed_step: str = Field(description="Final workflow step or synthetic outcome.")
    error_count: int = Field(ge=0, description="Number of errors observed during the scenario.")
    evidence: list[str] = Field(
        default_factory=list,
        description="Key evidence explaining the scenario outcome.",
    )
    findings: list[str] = Field(
        default_factory=list,
        description="Specific findings or mismatches captured by the suite.",
    )


class HardeningReport(BaseModel):
    """Structured report emitted by the Sprint 12 hardening suite."""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    project_path: str = Field(description="Project path used for deploy-oriented scenarios.")
    total_scenarios: int = Field(ge=0)
    passed_scenarios: int = Field(ge=0)
    failed_scenarios: int = Field(ge=0)
    readiness_score: float = Field(ge=0.0, le=1.0)
    scenarios: list[HardeningScenarioResult] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    observability: WorkflowObservabilitySummary = Field(
        description="Observability snapshot captured during the isolated suite run."
    )


class HardeningReportStore:
    """In-memory store for the latest generated hardening report."""

    def __init__(self) -> None:
        self._latest: HardeningReport | None = None
        self._lock = Lock()

    def record(self, report: HardeningReport) -> HardeningReport:
        stored = report.model_copy(deep=True)
        with self._lock:
            self._latest = stored
        return stored.model_copy(deep=True)

    def latest(self) -> HardeningReport | None:
        with self._lock:
            report = self._latest
        return report.model_copy(deep=True) if report is not None else None

    def reset(self) -> None:
        with self._lock:
            self._latest = None


class _NoOpStreamClient:
    """Minimal stream client used for direct local hardening execution."""

    async def xadd(
        self,
        name: str,
        fields: Mapping[str, str],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        del name, fields, maxlen, approximate
        return "0-0"

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> object:
        del name, groupname, id, mkstream
        return True

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        count: int = 1,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, Mapping[bytes | str, bytes | str]]]]]:
        del groupname, consumername, streams, count, block
        return []

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        del name, groupname
        return len(ids)

    async def close(self) -> None:
        return None


class _StubLLMProvider:
    def __init__(self, response: LLMResponse) -> None:
        self._response = response

    async def complete(
        self,
        *,
        prompt: str,
        task_id: str,
        agent: str,
        expected_format: str,
    ) -> LLMResponse:
        del prompt, task_id, agent, expected_format
        return self._response


hardening_store = HardeningReportStore()


async def run_hardening_suite(
    *,
    settings: Settings,
    project_path: str | Path,
    max_iterations: int = 3,
    stream_client: SupportsRedisStreams | None = None,
) -> HardeningReport:
    """Run the Sprint 12 fault-injection and fail-safe validation suite."""

    project_root = str(Path(project_path).expanduser().resolve())
    client = stream_client if stream_client is not None else _NoOpStreamClient()
    message_bus = MessageBus(settings=settings, stream_client=client)
    scenarios: list[HardeningScenarioResult] = []

    with _isolated_runtime_state():
        dependencies = build_default_deploy_dependencies(settings, message_bus)
        scenarios.append(
            await _run_deploy_specialist_failure_scenario(
                project_root=project_root,
                dependencies=dependencies,
            )
        )
        scenarios.append(
            await _run_low_confidence_retry_scenario(
                project_root=project_root,
                dependencies=dependencies,
                max_iterations=max_iterations,
            )
        )
        scenarios.append(
            await _run_unsupported_workflow_scenario(
                project_root=project_root,
                dependencies=dependencies,
            )
        )
        scenarios.append(
            await _run_incident_approval_gate_scenario(dependencies=dependencies)
        )
        scenarios.append(await _run_llm_evidence_guard_scenario(settings=settings))
        suite_observability = observability_store.summary()

    report = _build_report(project_root, scenarios, suite_observability)
    return hardening_store.record(report)


async def _run_deploy_specialist_failure_scenario(
    *,
    project_root: str,
    dependencies: DeployWorkflowDependencies,
) -> HardeningScenarioResult:
    async def failing_docker_generator(
        scan_result: CodebaseScanResult,
    ) -> DockerGenerationResult:
        del scan_result
        raise RuntimeError("docker generation failed")

    scenario_dependencies = replace(
        dependencies,
        docker_generator=failing_docker_generator,
    )
    state = SwarmState(
        task_id=f"hardening-deploy-failure-{uuid4().hex[:8]}",
        workflow_type="deploy",
        project_path=project_root,
        max_iterations=2,
    )
    result = await build_swarm_graph(scenario_dependencies).ainvoke(state)
    passed = (
        result.current_step == "error"
        and result.step_iterations.get("config_generation") == 2
        and any("docker generation failed" in error for error in result.errors)
    )
    findings = [] if passed else ["Deploy workflow did not halt safely after specialist failure."]
    return HardeningScenarioResult(
        name="deploy_specialist_failure",
        description="Inject a Docker specialist failure and verify retry-budget enforcement.",
        passed=passed,
        expected_outcome="Workflow retries within budget and ends in a safe error state.",
        observed_step=result.current_step,
        error_count=len(result.errors),
        evidence=result.errors[-3:] + _agent_evidence(result, "docker_specialist"),
        findings=findings,
    )


async def _run_low_confidence_retry_scenario(
    *,
    project_root: str,
    dependencies: DeployWorkflowDependencies,
    max_iterations: int,
) -> HardeningScenarioResult:
    async def low_confidence_docker_generator(
        scan_result: CodebaseScanResult,
    ) -> DockerGenerationResult:
        result = await dependencies.docker_generator(scan_result)
        return result.model_copy(update={"confidence": 0.51})

    async def low_confidence_k8s_generator(
        scan_result: CodebaseScanResult,
    ) -> KubernetesGenerationResult:
        result = await dependencies.k8s_generator(scan_result)
        return result.model_copy(update={"confidence": 0.54})

    async def low_confidence_cicd_generator(
        scan_result: CodebaseScanResult,
    ) -> CICDGenerationResult:
        result = await dependencies.cicd_generator(scan_result)
        return result.model_copy(update={"confidence": 0.58})

    scenario_dependencies = replace(
        dependencies,
        docker_generator=low_confidence_docker_generator,
        k8s_generator=low_confidence_k8s_generator,
        cicd_generator=low_confidence_cicd_generator,
    )
    state = SwarmState(
        task_id=f"hardening-low-confidence-{uuid4().hex[:8]}",
        workflow_type="deploy",
        project_path=project_root,
        max_iterations=max_iterations,
    )
    result = await build_swarm_graph(scenario_dependencies).ainvoke(state)
    passed = (
        result.current_step == "error"
        and result.step_iterations.get("config_generation") == max_iterations
        and any("confidence" in error.lower() for error in result.errors)
    )
    findings = [] if passed else ["Low-confidence outputs were not escalated into a safe stop."]
    return HardeningScenarioResult(
        name="deploy_low_confidence_retry_budget",
        description="Lower specialist confidence below threshold and verify bounded retries.",
        passed=passed,
        expected_outcome="Captain retries config generation and halts after the retry budget.",
        observed_step=result.current_step,
        error_count=len(result.errors),
        evidence=result.errors[-3:] + _agent_evidence(result, "captain"),
        findings=findings,
    )


async def _run_unsupported_workflow_scenario(
    *,
    project_root: str,
    dependencies: DeployWorkflowDependencies,
) -> HardeningScenarioResult:
    state = SwarmState(
        task_id=f"hardening-unsupported-{uuid4().hex[:8]}",
        workflow_type="chaos",
        project_path=project_root,
    )
    result = await build_swarm_graph(dependencies).ainvoke(state)
    passed = (
        result.current_step == "error"
        and any("unsupported_workflow_type" in error for error in result.errors)
    )
    findings = [] if passed else ["Unsupported workflow routing was not rejected explicitly."]
    return HardeningScenarioResult(
        name="unsupported_workflow_rejection",
        description="Send an unsupported workflow type through the router.",
        passed=passed,
        expected_outcome=(
            "The orchestrator rejects unsupported workflow types without side effects."
        ),
        observed_step=result.current_step,
        error_count=len(result.errors),
        evidence=result.errors[-3:],
        findings=findings,
    )


async def _run_incident_approval_gate_scenario(
    *,
    dependencies: DeployWorkflowDependencies,
) -> HardeningScenarioResult:
    state = SwarmState(
        task_id=f"hardening-incident-{uuid4().hex[:8]}",
        workflow_type="incident",
        alert_data={
            "service": "payments-api",
            "anomalies": ["high errors", "latency spike"],
            "error_rate": 0.12,
            "latency_p95_ms": 980.0,
            "restart_count": 2.0,
            "error_log_count": 5,
        },
        sandbox_test_passed=False,
    )
    result = await build_swarm_graph(dependencies).ainvoke(state)
    pending_requests = approval_store.list_requests(status="pending")
    passed = (
        result.current_step == "approval_requested"
        and result.approval_status == "pending"
        and result.requires_human_approval
        and len(pending_requests) == 1
    )
    findings = [] if passed else ["High-risk incident did not remain gated behind approval."]
    return HardeningScenarioResult(
        name="incident_approval_gate",
        description="Inject a severe incident and verify that live action stays approval-gated.",
        passed=passed,
        expected_outcome=(
            "The incident workflow requests human approval instead of auto-remediating."
        ),
        observed_step=result.current_step,
        error_count=len(result.errors),
        evidence=_pending_request_evidence(pending_requests) + _agent_evidence(result, "captain"),
        findings=findings,
    )


async def _run_llm_evidence_guard_scenario(
    *,
    settings: Settings,
) -> HardeningScenarioResult:
    client = LLMClient(
        settings,
        provider=_StubLLMProvider(
            LLMResponse(
                data={"summary": "unsafe"},
                evidence=[],
                confidence=0.94,
            )
        ),
    )
    try:
        await client.complete(
            prompt="Summarize rollout safety.",
            task_id=f"hardening-llm-{uuid4().hex[:8]}",
            agent="captain",
            expected_format="json",
        )
    except InsufficientEvidenceError as exc:
        return HardeningScenarioResult(
            name="llm_evidence_guard",
            description="Feed the shared LLM wrapper a response with no evidence.",
            passed=True,
            expected_outcome="The evidence guard rejects unevidenced model output.",
            observed_step="llm_guard_blocked",
            error_count=1,
            evidence=[str(exc)],
            findings=[],
        )

    return HardeningScenarioResult(
        name="llm_evidence_guard",
        description="Feed the shared LLM wrapper a response with no evidence.",
        passed=False,
        expected_outcome="The evidence guard rejects unevidenced model output.",
        observed_step="llm_guard_bypassed",
        error_count=0,
        evidence=[],
        findings=["LLM evidence validation unexpectedly accepted an unsafe response."],
    )


def _build_report(
    project_root: str,
    scenarios: list[HardeningScenarioResult],
    suite_observability: WorkflowObservabilitySummary,
) -> HardeningReport:
    total = len(scenarios)
    passed = sum(1 for scenario in scenarios if scenario.passed)
    failed = total - passed
    recommendations = _recommendations_for(scenarios)
    return HardeningReport(
        project_path=project_root,
        total_scenarios=total,
        passed_scenarios=passed,
        failed_scenarios=failed,
        readiness_score=(passed / total) if total else 0.0,
        scenarios=scenarios,
        recommendations=recommendations,
        observability=suite_observability,
    )


def _recommendations_for(
    scenarios: list[HardeningScenarioResult],
) -> list[str]:
    failed_names = [scenario.name for scenario in scenarios if not scenario.passed]
    if not failed_names:
        return [
            "Keep the hardening suite in CI so failure-mode regressions are caught early.",
            (
                "Extend fault injection next with live dependency timeouts "
                "and partial sandbox outages."
            ),
        ]
    return [
        "Investigate failed hardening scenarios before enabling more automated remediation.",
        f"Focus first on: {', '.join(failed_names)}.",
    ]


def _agent_evidence(state: SwarmState, agent_name: str) -> list[str]:
    result = state.agent_results.get(agent_name)
    if result is None:
        return []
    return result.evidence[-3:]


def _pending_request_evidence(requests: list[ApprovalRequest]) -> list[str]:
    if not requests:
        return []
    latest = requests[0]
    return [
        f"Approval request {latest.id} is {latest.status}.",
        f"Severity: {latest.severity}.",
        f"Proposed action: {latest.proposed_action}",
    ]


@contextmanager
def _isolated_runtime_state() -> Iterator[None]:
    approval_snapshot = approval_store.snapshot()
    observability_snapshot = observability_store.snapshot()
    approval_store.reset()
    observability_store.reset()
    try:
        yield
    finally:
        approval_store.restore(approval_snapshot)
        observability_store.restore(observability_snapshot)
