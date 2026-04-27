from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from swarm.agents.base import BaseAgent
from swarm.agents.k8s_specialist.kubectl_client import KubectlClient
from swarm.agents.sandbox_tester.smoke_tests import SmokeTestSummary, run_smoke_tests
from swarm.agents.sandbox_tester.vcluster_client import VClusterClient
from swarm.core.config import Settings
from swarm.core.events import EventType, SwarmEvent
from swarm.core.message_bus import MessageBus


class SandboxValidationResult(BaseModel):
    """Structured output from sandbox validation."""

    sandbox_cluster_id: str = Field(description="Identifier of the ephemeral sandbox cluster.")
    namespace: str = Field(description="Namespace used during sandbox validation.")
    apply_results: dict[str, str] = Field(
        default_factory=dict,
        description="Per-manifest apply output from the sandbox cluster.",
    )
    smoke_test: SmokeTestSummary = Field(description="Aggregate smoke-test outcome.")
    pod_status: dict[str, str] | None = Field(
        default=None,
        description="Observed pod status when a pod was inspected.",
    )
    pod_logs: str | None = Field(
        default=None,
        description="Collected pod logs when available.",
    )
    events: list[dict[str, str]] = Field(
        default_factory=list,
        description="Namespace events observed during validation.",
    )
    cleaned_up: bool = Field(
        description="Whether the sandbox cluster was deleted after validation.",
    )


class SandboxTesterAgent(BaseAgent):
    """Sandbox verification agent for isolated deployment validation."""

    agent_name = "sandbox_tester"

    def __init__(
        self,
        settings: Settings,
        message_bus: MessageBus,
        vcluster_client: VClusterClient | None = None,
        kubectl_client: KubectlClient | None = None,
    ) -> None:
        super().__init__(settings, message_bus)
        self.vcluster = vcluster_client or VClusterClient(settings=settings)
        self.kubectl = kubectl_client or KubectlClient(
            settings=settings.model_copy(
                update={
                    "dry_run_mode": False,
                    "require_human_approval": False,
                }
            )
        )

    async def validate_sandbox(
        self,
        *,
        task_id: str,
        manifests: dict[str, str],
        namespace: str,
        expected_port: int | None = None,
        pod_name: str | None = None,
        log_lines: int = 100,
        keep_sandbox: bool = False,
    ) -> SandboxValidationResult:
        cluster = await self.vcluster.create_sandbox(task_id)
        sandbox_kubectl = self.kubectl.for_kubeconfig(cluster.kubeconfig_path)
        apply_results: dict[str, str] = {}
        pod_status: dict[str, str] | None = None
        pod_logs: str | None = None
        events: list[dict[str, str]] = []
        cleaned_up = False
        smoke_test: SmokeTestSummary | None = None
        try:
            for manifest_name, manifest_content in manifests.items():
                apply_results[manifest_name] = await sandbox_kubectl.apply_manifest(
                    manifest_content,
                    dry_run=False,
                    task_id=task_id,
                    require_approval=False,
                )

            if pod_name is not None:
                pod_status = await sandbox_kubectl.get_pod_status(namespace, pod_name)
                pod_logs = await sandbox_kubectl.get_pod_logs(namespace, pod_name, lines=log_lines)
            events = await sandbox_kubectl.get_events(namespace)
            smoke_test = run_smoke_tests(
                manifests=manifests,
                expected_port=expected_port,
                pod_status=pod_status,
                pod_logs=pod_logs,
                events=events,
            )
        finally:
            if not keep_sandbox:
                await self.vcluster.delete_sandbox(cluster)
                cleaned_up = True
        if smoke_test is None:
            smoke_test = run_smoke_tests(manifests=manifests, expected_port=expected_port)
        return SandboxValidationResult(
            sandbox_cluster_id=cluster.cluster_id,
            namespace=namespace,
            apply_results=apply_results,
            smoke_test=smoke_test,
            pod_status=pod_status,
            pod_logs=pod_logs,
            events=events,
            cleaned_up=cleaned_up,
        )

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        if event.type != EventType.SANDBOX_TEST_REQUESTED:
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

        manifests = event.payload.get("manifests")
        if not isinstance(manifests, dict) or not all(
            isinstance(name, str) and isinstance(content, str)
            for name, content in manifests.items()
        ):
            return SwarmEvent(
                type=EventType.SANDBOX_TEST_FAILED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload={"error": "missing_or_invalid_manifests"},
                parent_event_id=event.id,
            )

        namespace = event.payload.get("namespace", self.settings.k8s_namespace)
        if not isinstance(namespace, str) or not namespace:
            namespace = self.settings.k8s_namespace
        pod_name = event.payload.get("pod_name")
        if not isinstance(pod_name, str) or not pod_name:
            pod_name = None
        expected_port = event.payload.get("expected_port")
        if not isinstance(expected_port, int):
            expected_port = None
        log_lines = event.payload.get("log_lines", 100)
        line_count = log_lines if isinstance(log_lines, int) and log_lines > 0 else 100
        keep_sandbox = event.payload.get("keep_sandbox") is True

        result = await self.validate_sandbox(
            task_id=event.task_id,
            manifests=manifests,
            namespace=namespace,
            expected_port=expected_port,
            pod_name=pod_name,
            log_lines=line_count,
            keep_sandbox=keep_sandbox,
        )
        response_type = (
            EventType.SANDBOX_TEST_PASSED
            if result.smoke_test.passed
            else EventType.SANDBOX_TEST_FAILED
        )
        return SwarmEvent(
            type=response_type,
            task_id=event.task_id,
            source_agent=self.agent_name,
            target_agent=event.source_agent,
            payload=result.model_dump(mode="json"),
            metadata={
                "sandbox_cluster_id": result.sandbox_cluster_id,
                "smoke_check_count": len(result.smoke_test.checks),
                "smoke_passed": result.smoke_test.passed,
            },
            parent_event_id=event.id,
        )

    async def health_check(self) -> dict[str, Any]:
        status = self.default_health_status()
        status["capabilities"] = [
            "sandbox_lifecycle",
            "manifest_apply",
            "smoke_test_validation",
        ]
        return status
