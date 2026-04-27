from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.core.approvals import approval_store
from forge.core.config import Settings
from forge.core.message_bus import MessageBus
from forge.orchestrator.graph import build_swarm_graph
from forge.orchestrator.state import SwarmState
from forge.orchestrator.workflows.deploy_workflow import (
    CICDGenerationResult,
    DeployWorkflowDependencies,
    DockerGenerationResult,
    KubernetesGenerationResult,
    build_default_deploy_dependencies,
    build_deploy_workflow,
)
from tests.conftest import FakeRedisStreamClient


def _build_dependencies(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
    *,
    docker_confidence: float = 0.95,
    k8s_confidence: float = 0.94,
    cicd_confidence: float = 0.93,
    docker_failure: bool = False,
) -> DeployWorkflowDependencies:
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    default_dependencies = build_default_deploy_dependencies(test_settings, bus)

    async def docker_generator(
        scan_result: CodebaseScanResult,
    ) -> DockerGenerationResult:
        if docker_failure:
            raise RuntimeError("docker generation failed")
        result = await default_dependencies.docker_generator(scan_result)
        return result.model_copy(update={"confidence": docker_confidence})

    async def k8s_generator(
        scan_result: CodebaseScanResult,
    ) -> KubernetesGenerationResult:
        result = await default_dependencies.k8s_generator(scan_result)
        return result.model_copy(update={"confidence": k8s_confidence})

    async def cicd_generator(
        scan_result: CodebaseScanResult,
    ) -> CICDGenerationResult:
        result = await default_dependencies.cicd_generator(scan_result)
        return result.model_copy(update={"confidence": cicd_confidence})

    return replace(
        default_dependencies,
        docker_generator=docker_generator,
        k8s_generator=k8s_generator,
        cicd_generator=cicd_generator,
    )


@pytest.fixture(autouse=True)
def reset_approval_store() -> None:
    approval_store.reset()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deploy_workflow_runs_start_to_finish_with_mock_agents(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
    python_fastapi_project: Path,
) -> None:
    dependencies = _build_dependencies(test_settings, fake_stream_client)
    graph = build_swarm_graph(dependencies)
    initial_state = SwarmState(
        task_id="workflow-1",
        workflow_type="deploy",
        project_path=str(python_fastapi_project),
    )

    result = await graph.ainvoke(initial_state)

    assert result.current_step == "deployment_plan_ready"
    assert result.agent_results["librarian"].success
    assert result.agent_results["docker_specialist"].success
    assert result.dockerfile is not None
    assert "FROM python:3.11-slim" in result.dockerfile
    assert "deployment.yaml" in result.k8s_manifests
    assert "containerPort: 8000" in result.k8s_manifests["deployment.yaml"]
    assert result.cicd_pipeline is not None
    assert "actions/setup-python@v5" in result.cicd_pipeline
    assert "deployment_plan_ready" in result.completed_steps


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orchestrator_halts_after_repeated_generation_failure(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
    python_fastapi_project: Path,
) -> None:
    dependencies = _build_dependencies(
        test_settings,
        fake_stream_client,
        docker_failure=True,
    )
    graph = build_swarm_graph(dependencies)
    initial_state = SwarmState(
        task_id="workflow-2",
        workflow_type="deploy",
        project_path=str(python_fastapi_project),
        max_iterations=2,
    )

    result = await graph.ainvoke(initial_state)

    assert result.current_step == "error"
    assert result.step_iterations["config_generation"] == 2
    assert result.agent_results["k8s_specialist"].success
    assert result.agent_results["cicd_specialist"].success
    assert any("docker generation failed" in error for error in result.errors)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_iteration_guard_stops_low_confidence_retries(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
    python_fastapi_project: Path,
) -> None:
    dependencies = _build_dependencies(
        test_settings,
        fake_stream_client,
        docker_confidence=0.5,
        k8s_confidence=0.55,
        cicd_confidence=0.6,
    )
    graph = build_swarm_graph(dependencies)
    initial_state = SwarmState(
        task_id="workflow-3",
        workflow_type="deploy",
        project_path=str(python_fastapi_project),
        max_iterations=3,
    )

    result = await graph.ainvoke(initial_state)

    assert result.current_step == "error"
    assert result.step_iterations["config_generation"] == 3
    assert result.iteration_count == 2
    assert any("confidence" in error.lower() for error in result.errors)


@pytest.mark.unit
def test_graph_visualization_is_available_when_pygraphviz_is_installed(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> None:
    dependencies = _build_dependencies(test_settings, fake_stream_client)
    graph = build_deploy_workflow(dependencies)

    try:
        png_bytes = graph.get_graph().draw_png(None)
    except ImportError:
        pytest.skip("pygraphviz is not installed in this environment.")

    assert png_bytes


@pytest.mark.unit
@pytest.mark.asyncio
async def test_incident_workflow_requests_human_approval_for_high_severity_alert(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> None:
    dependencies = _build_dependencies(test_settings, fake_stream_client)
    graph = build_swarm_graph(dependencies)
    initial_state = SwarmState(
        task_id="incident-workflow-1",
        workflow_type="incident",
        alert_data={
            "service": "api",
            "anomalies": ["high errors", "high latency"],
            "error_rate": 0.11,
            "latency_p95_ms": 920.0,
            "restart_count": 2.0,
            "error_log_count": 4,
        },
        sandbox_test_passed=False,
    )

    result = await graph.ainvoke(initial_state)

    assert result.current_step == "approval_requested"
    assert result.approval_status == "pending"
    assert result.requires_human_approval is True
    assert result.root_cause_hypothesis is not None
    assert result.alert_data["approval_request_id"]
    approval_requests = approval_store.list_requests(status="pending")
    assert len(approval_requests) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_incident_workflow_can_finish_in_observation_mode(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> None:
    dependencies = _build_dependencies(test_settings, fake_stream_client)
    graph = build_swarm_graph(dependencies)
    initial_state = SwarmState(
        task_id="incident-workflow-2",
        workflow_type="incident",
        alert_data={
            "service": "api",
            "anomalies": [],
            "error_rate": 0.0,
            "latency_p95_ms": 110.0,
            "restart_count": 0.0,
            "error_log_count": 0,
        },
        sandbox_test_passed=True,
    )

    result = await graph.ainvoke(initial_state)

    assert result.current_step == "incident_observation_complete"
    assert result.approval_status is None
    assert result.requires_human_approval is False
    assert approval_store.list_requests() == []
