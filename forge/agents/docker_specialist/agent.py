from __future__ import annotations

from typing import Any

from forge.agents.base import BaseAgent
from forge.agents.docker_specialist.generators import generate_docker_assets
from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.core.events import EventType, SwarmEvent
from forge.orchestrator.workflows.deploy_workflow import DockerGenerationResult


class DockerSpecialistAgent(BaseAgent):
    """Container generation agent for Sprint 4 deployment assets."""

    agent_name = "docker_specialist"

    async def generate_artifacts(
        self,
        scan_result: CodebaseScanResult,
    ) -> DockerGenerationResult:
        bundle = generate_docker_assets(scan_result)
        return DockerGenerationResult(
            dockerfile=bundle.dockerfile,
            docker_compose=bundle.docker_compose,
            evidence=bundle.evidence,
            confidence=bundle.confidence,
        )

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        if event.type != EventType.DEPLOYMENT_PLAN_REQUESTED:
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

        scan_payload = event.payload.get("scan_result", event.payload)
        scan_result = CodebaseScanResult.model_validate(scan_payload)
        result = await self.generate_artifacts(scan_result)
        return SwarmEvent(
            type=EventType.DOCKERFILE_GENERATED,
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

    async def health_check(self) -> dict[str, Any]:
        status = self.default_health_status()
        status["capabilities"] = ["dockerfile_generation", "compose_generation"]
        return status
