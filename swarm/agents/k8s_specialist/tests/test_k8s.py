from __future__ import annotations

from pathlib import Path

import pytest

from swarm.agents.k8s_specialist.agent import K8sSpecialistAgent
from swarm.agents.k8s_specialist.kubectl_client import CommandResult, KubectlClient
from swarm.agents.k8s_specialist.manifest_generator import generate_manifests
from swarm.agents.librarian.ast_analyzer import ASTAnalyzer
from swarm.core.config import Settings
from swarm.core.events import EventType, SwarmEvent
from swarm.core.exceptions import ConfigurationError
from swarm.core.message_bus import MessageBus
from tests.conftest import FakeRedisStreamClient


class StubKubectlRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = results
        self.calls: list[tuple[list[str], str | None]] = []

    async def run(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> CommandResult:
        self.calls.append((args, input_text))
        return self.results.pop(0)


@pytest.mark.unit
def test_generate_k8s_manifests_for_fastapi_project(
    python_fastapi_project: Path,
) -> None:
    scan_result = ASTAnalyzer().analyze_project(python_fastapi_project)

    bundle = generate_manifests(scan_result, namespace="apps")

    assert "deployment.yaml" in bundle.manifests
    assert "service.yaml" in bundle.manifests
    assert "configmap.yaml" in bundle.manifests
    assert "namespace: apps" in bundle.manifests["deployment.yaml"]
    assert "containerPort: 8000" in bundle.manifests["deployment.yaml"]
    assert "path: /health" in bundle.manifests["deployment.yaml"]
    assert "DATABASE_URL" in bundle.manifests["configmap.yaml"]
    assert "SECRET_KEY" in bundle.manifests["configmap.yaml"]
    assert bundle.confidence >= 0.9


@pytest.mark.unit
def test_generate_k8s_manifests_for_go_project(
    go_service_project: Path,
) -> None:
    scan_result = ASTAnalyzer().analyze_project(go_service_project)

    bundle = generate_manifests(scan_result)

    assert "containerPort: 8080" in bundle.manifests["deployment.yaml"]
    assert "targetPort: 8080" in bundle.manifests["service.yaml"]
    assert "REDIS_URL" in bundle.manifests["configmap.yaml"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_kubectl_client_parses_pod_status(
    test_settings: Settings,
) -> None:
    runner = StubKubectlRunner(
        [
            CommandResult(
                stdout=(
                    '{"metadata":{"name":"api-123","namespace":"apps"},'
                    '"spec":{"nodeName":"node-1"},'
                    '"status":{"phase":"Running","podIP":"10.0.0.8",'
                    '"containerStatuses":[{"ready":true,"restartCount":1}]}}'
                ),
                stderr="",
                returncode=0,
            )
        ]
    )
    client = KubectlClient(settings=test_settings, runner=runner)

    status = await client.get_pod_status("apps", "api-123")

    assert status["phase"] == "Running"
    assert status["ready"] == "1/1"
    assert status["restart_count"] == "1"
    assert runner.calls[0][0][-4:] == ["--namespace", "apps", "-o", "json"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_kubectl_client_uses_dry_run_for_manifest_apply(
    test_settings: Settings,
) -> None:
    runner = StubKubectlRunner(
        [CommandResult(stdout="deployment.apps/api configured (dry run)", stderr="", returncode=0)]
    )
    client = KubectlClient(settings=test_settings, runner=runner)

    result = await client.apply_manifest("apiVersion: apps/v1\nkind: Deployment\n", task_id="t-1")

    assert "dry run" in result
    assert "--dry-run=server" in runner.calls[0][0]
    assert runner.calls[0][1] is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_kubectl_client_blocks_live_apply_when_approval_is_required(
    test_settings: Settings,
) -> None:
    runner = StubKubectlRunner([])
    live_settings = test_settings.model_copy(update={"dry_run_mode": False})
    client = KubectlClient(settings=live_settings, runner=runner)

    with pytest.raises(ConfigurationError):
        await client.apply_manifest("kind: ConfigMap\n", dry_run=False, task_id="t-2")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_k8s_specialist_validates_manifests_from_task_assignment(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> None:
    runner = StubKubectlRunner(
        [CommandResult(stdout="deployment.apps/api created (dry run)", stderr="", returncode=0)]
    )
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    agent = K8sSpecialistAgent(
        settings=test_settings,
        message_bus=bus,
        kubectl_client=KubectlClient(settings=test_settings, runner=runner),
    )
    event = SwarmEvent(
        type=EventType.TASK_ASSIGNED,
        task_id="runtime-1",
        source_agent="captain",
        target_agent="k8s_specialist",
        payload={
            "action": "validate_manifests",
            "manifests": {"deployment.yaml": "kind: Deployment\n"},
        },
    )

    result = await agent.process_event(event)

    assert result is not None
    assert result.type == EventType.TASK_COMPLETED
    assert result.payload["action"] == "validate_manifests"
    assert result.payload["dry_run"] is True
    validation_results = result.payload["validation_results"]
    assert isinstance(validation_results, dict)
    assert "deployment.yaml" in validation_results


@pytest.mark.unit
@pytest.mark.asyncio
async def test_k8s_specialist_inspects_pod_and_returns_logs(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> None:
    runner = StubKubectlRunner(
        [
            CommandResult(
                stdout=(
                    '{"metadata":{"name":"api-123","namespace":"apps"},'
                    '"status":{"phase":"Running","podIP":"10.0.0.8",'
                    '"containerStatuses":[{"ready":true,"restartCount":0}]}}'
                ),
                stderr="",
                returncode=0,
            ),
            CommandResult(stdout="started\nhealthy", stderr="", returncode=0),
        ]
    )
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    agent = K8sSpecialistAgent(
        settings=test_settings,
        message_bus=bus,
        kubectl_client=KubectlClient(settings=test_settings, runner=runner),
    )
    event = SwarmEvent(
        type=EventType.TASK_ASSIGNED,
        task_id="runtime-2",
        source_agent="captain",
        target_agent="k8s_specialist",
        payload={
            "action": "inspect_pod",
            "namespace": "apps",
            "pod_name": "api-123",
            "lines": 20,
        },
    )

    result = await agent.process_event(event)

    assert result is not None
    assert result.type == EventType.TASK_COMPLETED
    assert result.payload["pod_name"] == "api-123"
    assert result.payload["status"]["phase"] == "Running"
    assert "healthy" in result.payload["logs"]
