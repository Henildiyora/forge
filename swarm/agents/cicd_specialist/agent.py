from __future__ import annotations

from typing import Any

from swarm.agents.base import BaseAgent
from swarm.agents.cicd_specialist.pipeline_generators import generate_pipeline
from swarm.agents.librarian.ast_analyzer import CodebaseScanResult
from swarm.core.events import EventType, SwarmEvent
from swarm.orchestrator.workflows.deploy_workflow import CICDGenerationResult


class CICDSpecialistAgent(BaseAgent):
    """Pipeline generation agent for Sprint 4."""

    agent_name = "cicd_specialist"

    async def generate_artifacts(
        self,
        scan_result: CodebaseScanResult,
    ) -> CICDGenerationResult:
        bundle = generate_pipeline(scan_result)
        return CICDGenerationResult(
            pipeline=bundle.pipeline,
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
            type=EventType.CICD_PIPELINE_GENERATED,
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
        status["capabilities"] = ["pipeline_generation"]
        return status
