from __future__ import annotations

from typing import Any

from forge.agents.base import BaseAgent
from forge.agents.k8s_specialist.kubectl_client import KubectlClient
from forge.agents.k8s_specialist.manifest_generator import generate_manifests
from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus
from forge.orchestrator.workflows.deploy_workflow import KubernetesGenerationResult


class K8sSpecialistAgent(BaseAgent):
    """Kubernetes manifest and cluster-operations agent."""

    agent_name = "k8s_specialist"

    def __init__(
        self,
        settings: Settings,
        message_bus: MessageBus,
        kubectl_client: KubectlClient | None = None,
    ) -> None:
        super().__init__(settings, message_bus)
        self.kubectl = kubectl_client or KubectlClient(settings=settings)

    async def generate_artifacts(
        self,
        scan_result: CodebaseScanResult,
    ) -> KubernetesGenerationResult:
        bundle = generate_manifests(
            scan_result,
            namespace=self.settings.k8s_namespace,
        )
        return KubernetesGenerationResult(
            manifests=bundle.manifests,
            evidence=bundle.evidence,
            confidence=bundle.confidence,
        )

    async def validate_manifests(
        self,
        manifests: dict[str, str],
        *,
        task_id: str | None = None,
    ) -> dict[str, str]:
        validation_results: dict[str, str] = {}
        for manifest_name, manifest_content in manifests.items():
            validation_results[manifest_name] = await self.kubectl.apply_manifest(
                manifest_content,
                dry_run=True,
                task_id=task_id,
            )
        return validation_results

    async def inspect_pod(
        self,
        *,
        namespace: str,
        pod_name: str,
        lines: int = 100,
    ) -> dict[str, object]:
        status = await self.kubectl.get_pod_status(namespace, pod_name)
        logs = await self.kubectl.get_pod_logs(namespace, pod_name, lines=lines)
        return {
            "status": status,
            "logs": logs,
        }

    async def inspect_namespace_events(self, namespace: str) -> list[dict[str, str]]:
        return await self.kubectl.get_events(namespace)

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        if event.type == EventType.DEPLOYMENT_PLAN_REQUESTED:
            scan_payload = event.payload.get("scan_result", event.payload)
            scan_result = CodebaseScanResult.model_validate(scan_payload)
            result = await self.generate_artifacts(scan_result)
            return SwarmEvent(
                type=EventType.K8S_MANIFESTS_GENERATED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload=result.model_dump(mode="json"),
                metadata={
                    "confidence": result.confidence,
                    "evidence_count": len(result.evidence),
                },
                parent_event_id=event.id,
            )

        if event.type == EventType.TASK_ASSIGNED:
            return await self._handle_runtime_task(event)

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

    async def health_check(self) -> dict[str, Any]:
        status = self.default_health_status()
        status["capabilities"] = [
            "manifest_generation",
            "manifest_validation",
            "pod_status_lookup",
            "pod_log_lookup",
            "cluster_event_lookup",
        ]
        return status

    async def _handle_runtime_task(self, event: SwarmEvent) -> SwarmEvent:
        action = event.payload.get("action")
        if action == "validate_manifests":
            manifests = event.payload.get("manifests")
            if not isinstance(manifests, dict) or not all(
                isinstance(name, str) and isinstance(content, str)
                for name, content in manifests.items()
            ):
                return SwarmEvent(
                    type=EventType.TASK_FAILED,
                    task_id=event.task_id,
                    source_agent=self.agent_name,
                    target_agent=event.source_agent,
                    payload={"error": "missing_or_invalid_manifests"},
                    parent_event_id=event.id,
                )
            validation = await self.validate_manifests(manifests, task_id=event.task_id)
            return SwarmEvent(
                type=EventType.TASK_COMPLETED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload={
                    "action": action,
                    "validation_results": validation,
                    "dry_run": True,
                },
                parent_event_id=event.id,
            )

        if action == "inspect_pod":
            namespace = event.payload.get("namespace", self.settings.k8s_namespace)
            pod_name = event.payload.get("pod_name")
            if not isinstance(namespace, str) or not namespace:
                namespace = self.settings.k8s_namespace
            if not isinstance(pod_name, str) or not pod_name:
                return SwarmEvent(
                    type=EventType.TASK_FAILED,
                    task_id=event.task_id,
                    source_agent=self.agent_name,
                    target_agent=event.source_agent,
                    payload={"error": "missing_pod_name"},
                    parent_event_id=event.id,
                )
            lines = event.payload.get("lines", 100)
            line_count = lines if isinstance(lines, int) and lines > 0 else 100
            inspection = await self.inspect_pod(
                namespace=namespace,
                pod_name=pod_name,
                lines=line_count,
            )
            return SwarmEvent(
                type=EventType.TASK_COMPLETED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload={
                    "action": action,
                    "namespace": namespace,
                    "pod_name": pod_name,
                    **inspection,
                },
                parent_event_id=event.id,
            )

        if action == "list_events":
            namespace = event.payload.get("namespace", self.settings.k8s_namespace)
            if not isinstance(namespace, str) or not namespace:
                namespace = self.settings.k8s_namespace
            events = await self.inspect_namespace_events(namespace)
            return SwarmEvent(
                type=EventType.TASK_COMPLETED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload={
                    "action": action,
                    "namespace": namespace,
                    "events": events,
                },
                parent_event_id=event.id,
            )

        return SwarmEvent(
            type=EventType.TASK_FAILED,
            task_id=event.task_id,
            source_agent=self.agent_name,
            target_agent=event.source_agent,
            payload={"error": "unsupported_action", "action": action},
            parent_event_id=event.id,
        )
