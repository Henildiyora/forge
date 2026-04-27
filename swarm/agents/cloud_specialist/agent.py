from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from swarm.agents.base import BaseAgent
from swarm.agents.cloud_specialist.mcp_client import (
    CloudDeploymentAssessment,
    CloudEnvironmentSummary,
    CloudProvider,
    MCPClient,
    MCPResource,
)
from swarm.core.config import Settings
from swarm.core.events import EventType, SwarmEvent
from swarm.core.message_bus import MessageBus


class CloudTaskResult(BaseModel):
    """Normalized payload returned by the Cloud Specialist."""

    action: str
    provider: CloudProvider
    resources: list[MCPResource] = Field(default_factory=list)
    summary: CloudEnvironmentSummary | None = None
    assessment: CloudDeploymentAssessment | None = None
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class CloudSpecialistAgent(BaseAgent):
    """Read-only cloud inventory and assessment agent."""

    agent_name = "cloud_specialist"

    def __init__(
        self,
        settings: Settings,
        message_bus: MessageBus,
        mcp_client: MCPClient | None = None,
    ) -> None:
        super().__init__(settings, message_bus)
        self.mcp = mcp_client or MCPClient()

    async def inventory_environment(
        self,
        *,
        provider: CloudProvider,
        account_id: str | None = None,
        region: str | None = None,
    ) -> CloudTaskResult:
        resources = await self.mcp.list_resources(provider, account_id=account_id, region=region)
        summary = await self.mcp.summarize_environment(
            provider,
            account_id=account_id,
            region=region,
        )
        return CloudTaskResult(
            action="inventory_environment",
            provider=provider,
            resources=resources,
            summary=summary,
            evidence=summary.evidence,
            confidence=summary.confidence,
        )

    async def assess_environment(
        self,
        *,
        provider: CloudProvider,
        target_service: str,
        account_id: str | None = None,
        region: str | None = None,
        deployment_context: dict[str, object] | None = None,
    ) -> CloudTaskResult:
        assessment = await self.mcp.assess_deployment_target(
            provider,
            target_service=target_service,
            account_id=account_id,
            region=region,
            deployment_context=deployment_context,
        )
        return CloudTaskResult(
            action="assess_deployment_target",
            provider=provider,
            assessment=assessment,
            evidence=assessment.evidence,
            confidence=assessment.confidence,
        )

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        if event.type != EventType.TASK_ASSIGNED:
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

        action = event.payload.get("action")
        provider_value = event.payload.get("provider")
        provider = _provider(provider_value)
        if provider is None:
            return SwarmEvent(
                type=EventType.TASK_FAILED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload={"error": "missing_or_invalid_provider"},
                parent_event_id=event.id,
            )

        account_id = event.payload.get("account_id")
        region = event.payload.get("region")
        if not isinstance(account_id, str):
            account_id = None
        if not isinstance(region, str):
            region = None

        if action == "inventory_environment":
            result = await self.inventory_environment(
                provider=provider,
                account_id=account_id,
                region=region,
            )
        elif action == "assess_deployment_target":
            target_service = event.payload.get("target_service")
            if not isinstance(target_service, str) or not target_service:
                return SwarmEvent(
                    type=EventType.TASK_FAILED,
                    task_id=event.task_id,
                    source_agent=self.agent_name,
                    target_agent=event.source_agent,
                    payload={"error": "missing_target_service"},
                    parent_event_id=event.id,
                )
            deployment_context = event.payload.get("deployment_context")
            context = deployment_context if isinstance(deployment_context, dict) else None
            result = await self.assess_environment(
                provider=provider,
                target_service=target_service,
                account_id=account_id,
                region=region,
                deployment_context=context,
            )
        else:
            return SwarmEvent(
                type=EventType.TASK_FAILED,
                task_id=event.task_id,
                source_agent=self.agent_name,
                target_agent=event.source_agent,
                payload={"error": "unsupported_action", "action": action},
                parent_event_id=event.id,
            )

        return SwarmEvent(
            type=EventType.TASK_COMPLETED,
            task_id=event.task_id,
            source_agent=self.agent_name,
            target_agent=event.source_agent,
            payload=result.model_dump(mode="json"),
            metadata={
                "confidence": result.confidence,
                "action": result.action,
            },
            parent_event_id=event.id,
        )

    async def health_check(self) -> dict[str, Any]:
        status = self.default_health_status()
        status["capabilities"] = [
            "cloud_inventory",
            "deployment_target_assessment",
            "mcp_read_only_operations",
        ]
        return status


def _provider(value: object) -> CloudProvider | None:
    if value == "aws":
        return "aws"
    if value == "gcp":
        return "gcp"
    if value == "azure":
        return "azure"
    return None
