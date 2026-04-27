from __future__ import annotations

from pathlib import Path

import pytest

from swarm.agents.k8s_specialist.kubectl_client import CommandResult, KubectlClient
from swarm.agents.librarian.ast_analyzer import ASTAnalyzer
from swarm.agents.sandbox_tester.agent import SandboxTesterAgent
from swarm.agents.sandbox_tester.smoke_tests import run_smoke_tests
from swarm.agents.sandbox_tester.vcluster_client import VClusterClient
from swarm.core.config import Settings
from swarm.core.events import EventType, SwarmEvent
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


class StubVClusterRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    async def run(self, args: list[str]) -> CommandResult:
        self.calls.append(args)
        return self.results.pop(0)


@pytest.mark.unit
def test_smoke_tests_pass_for_healthy_runtime_signals() -> None:
    summary = run_smoke_tests(
        manifests={
            "deployment.yaml": "ports:\n- containerPort: 8000\n",
            "service.yaml": "ports:\n- targetPort: 8000\n",
        },
        expected_port=8000,
        pod_status={"phase": "Running", "ready": "1/1", "restart_count": "0"},
        pod_logs="started\nhealthy",
        events=[{"type": "Normal", "message": "Created pod"}],
    )

    assert summary.passed is True
    assert all(check.passed for check in summary.checks)


@pytest.mark.unit
def test_smoke_tests_fail_for_warning_events_and_bad_logs() -> None:
    summary = run_smoke_tests(
        manifests={
            "deployment.yaml": "ports:\n- containerPort: 8000\n",
            "service.yaml": "ports:\n- targetPort: 8000\n",
        },
        expected_port=8000,
        pod_status={"phase": "CrashLoopBackOff", "ready": "0/1", "restart_count": "4"},
        pod_logs="panic: boom",
        events=[{"type": "Warning", "message": "BackOff"}],
    )

    assert summary.passed is False
    assert any(not check.passed for check in summary.checks)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vcluster_client_creates_and_deletes_sandbox(
    test_settings: Settings,
) -> None:
    runner = StubVClusterRunner(
        [
            CommandResult(stdout="created", stderr="", returncode=0),
            CommandResult(stdout="connected", stderr="", returncode=0),
            CommandResult(stdout="deleted", stderr="", returncode=0),
        ]
    )
    client = VClusterClient(settings=test_settings, runner=runner)

    cluster = await client.create_sandbox("deploy-123")
    delete_output = await client.delete_sandbox(cluster)

    assert cluster.cluster_id.startswith("sandbox-deploy-123")
    assert cluster.kubeconfig_path.endswith("-kubeconfig.yaml")
    assert runner.calls[0][:2] == ["create", cluster.cluster_id]
    assert runner.calls[1][:2] == ["connect", cluster.cluster_id]
    assert delete_output == "deleted"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_tester_passes_validation_for_healthy_sandbox(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
    python_fastapi_project: Path,
) -> None:
    scan_result = ASTAnalyzer().analyze_project(python_fastapi_project)
    manifests = {
        "deployment.yaml": "ports:\n- containerPort: 8000\n",
        "service.yaml": "ports:\n- targetPort: 8000\n",
    }
    vcluster_runner = StubVClusterRunner(
        [
            CommandResult(stdout="created", stderr="", returncode=0),
            CommandResult(stdout="connected", stderr="", returncode=0),
            CommandResult(stdout="deleted", stderr="", returncode=0),
        ]
    )
    kubectl_runner = StubKubectlRunner(
        [
            CommandResult(stdout="deployment applied", stderr="", returncode=0),
            CommandResult(stdout="service applied", stderr="", returncode=0),
            CommandResult(
                stdout=(
                    '{"metadata":{"name":"api-123","namespace":"devops-swarm"},'
                    '"status":{"phase":"Running","containerStatuses":[{"ready":true,'
                    '"restartCount":0}]}}'
                ),
                stderr="",
                returncode=0,
            ),
            CommandResult(stdout="app started", stderr="", returncode=0),
            CommandResult(
                stdout=(
                    '{"items":[{"type":"Normal","message":"Created pod",'
                    '"involvedObject":{"name":"api-123","kind":"Pod"}}]}'
                ),
                stderr="",
                returncode=0,
            ),
        ]
    )
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    agent = SandboxTesterAgent(
        settings=test_settings,
        message_bus=bus,
        vcluster_client=VClusterClient(settings=test_settings, runner=vcluster_runner),
        kubectl_client=KubectlClient(
            settings=test_settings.model_copy(
                update={"dry_run_mode": False, "require_human_approval": False}
            ),
            runner=kubectl_runner,
        ),
    )
    event = SwarmEvent(
        type=EventType.SANDBOX_TEST_REQUESTED,
        task_id="sandbox-1",
        source_agent="captain",
        target_agent="sandbox_tester",
        payload={
            "manifests": manifests,
            "namespace": test_settings.k8s_namespace,
            "pod_name": "api-123",
            "expected_port": scan_result.port,
        },
    )

    result = await agent.process_event(event)

    assert result is not None
    assert result.type == EventType.SANDBOX_TEST_PASSED
    assert result.payload["smoke_test"]["passed"] is True
    assert result.metadata["smoke_passed"] is True
    assert len(vcluster_runner.calls) == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_tester_fails_when_smoke_checks_fail(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> None:
    vcluster_runner = StubVClusterRunner(
        [
            CommandResult(stdout="created", stderr="", returncode=0),
            CommandResult(stdout="connected", stderr="", returncode=0),
            CommandResult(stdout="deleted", stderr="", returncode=0),
        ]
    )
    kubectl_runner = StubKubectlRunner(
        [
            CommandResult(stdout="deployment applied", stderr="", returncode=0),
            CommandResult(stdout="service applied", stderr="", returncode=0),
            CommandResult(
                stdout=(
                    '{"metadata":{"name":"api-123","namespace":"devops-swarm"},'
                    '"status":{"phase":"CrashLoopBackOff","containerStatuses":[{"ready":false,'
                    '"restartCount":3}]}}'
                ),
                stderr="",
                returncode=0,
            ),
            CommandResult(stdout="panic: boom", stderr="", returncode=0),
            CommandResult(
                stdout='{"items":[{"type":"Warning","message":"BackOff","involvedObject":{"name":"api-123","kind":"Pod"}}]}',
                stderr="",
                returncode=0,
            ),
        ]
    )
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    agent = SandboxTesterAgent(
        settings=test_settings,
        message_bus=bus,
        vcluster_client=VClusterClient(settings=test_settings, runner=vcluster_runner),
        kubectl_client=KubectlClient(
            settings=test_settings.model_copy(
                update={"dry_run_mode": False, "require_human_approval": False}
            ),
            runner=kubectl_runner,
        ),
    )
    event = SwarmEvent(
        type=EventType.SANDBOX_TEST_REQUESTED,
        task_id="sandbox-2",
        source_agent="captain",
        target_agent="sandbox_tester",
        payload={
            "manifests": {
                "deployment.yaml": "ports:\n- containerPort: 8000\n",
                "service.yaml": "ports:\n- targetPort: 8000\n",
            },
            "namespace": test_settings.k8s_namespace,
            "pod_name": "api-123",
            "expected_port": 8000,
        },
    )

    result = await agent.process_event(event)

    assert result is not None
    assert result.type == EventType.SANDBOX_TEST_FAILED
    assert result.payload["smoke_test"]["passed"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_tester_requires_manifest_payload(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> None:
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    agent = SandboxTesterAgent(settings=test_settings, message_bus=bus)
    event = SwarmEvent(
        type=EventType.SANDBOX_TEST_REQUESTED,
        task_id="sandbox-3",
        source_agent="captain",
        target_agent="sandbox_tester",
        payload={},
    )

    result = await agent.process_event(event)

    assert result is not None
    assert result.type == EventType.SANDBOX_TEST_FAILED
    assert result.payload["error"] == "missing_or_invalid_manifests"
