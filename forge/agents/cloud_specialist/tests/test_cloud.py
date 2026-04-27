from __future__ import annotations

import pytest

from forge.agents.cloud_specialist.agent import CloudSpecialistAgent
from forge.agents.cloud_specialist.mcp_client import MCPClient
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus
from tests.conftest import FakeRedisStreamClient


@pytest.fixture
def cloud_catalog() -> dict[str, list[dict[str, object]]]:
    return {
        "aws": [
            {
                "provider": "aws",
                "service": "eks",
                "resource_id": "eks-cluster-1",
                "name": "payments-eks",
                "region": "us-east-1",
                "account_id": "prod-123",
                "status": "active",
                "tags": {"service": "payments", "env": "prod"},
            },
            {
                "provider": "aws",
                "service": "ecr",
                "resource_id": "ecr-1",
                "name": "payments-registry",
                "region": "us-east-1",
                "account_id": "prod-123",
                "status": "available",
                "tags": {"service": "payments"},
            },
            {
                "provider": "aws",
                "service": "rds",
                "resource_id": "rds-1",
                "name": "payments-db",
                "region": "us-east-1",
                "account_id": "prod-123",
                "status": "available",
                "public_exposure": True,
                "tags": {"service": "payments"},
            },
        ],
        "gcp": [
            {
                "provider": "gcp",
                "service": "gke",
                "resource_id": "gke-1",
                "name": "analytics-gke",
                "region": "us-central1",
                "account_id": "analytics",
                "status": "running",
                "tags": {"service": "analytics"},
            }
        ],
    }


@pytest.fixture
def cloud_agent(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
    cloud_catalog: dict[str, list[dict[str, object]]],
) -> CloudSpecialistAgent:
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    return CloudSpecialistAgent(
        settings=test_settings,
        message_bus=bus,
        mcp_client=MCPClient(resource_catalog=cloud_catalog),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_client_summarizes_environment(
    cloud_catalog: dict[str, list[dict[str, object]]],
) -> None:
    client = MCPClient(resource_catalog=cloud_catalog)

    summary = await client.summarize_environment("aws", account_id="prod-123", region="us-east-1")

    assert summary.resource_count == 3
    assert summary.resources_by_service["eks"] == 1
    assert "rds-1" in summary.public_resources
    assert summary.confidence >= 0.9


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_client_assessment_flags_public_database_as_blocker(
    cloud_catalog: dict[str, list[dict[str, object]]],
) -> None:
    client = MCPClient(resource_catalog=cloud_catalog)

    assessment = await client.assess_deployment_target(
        "aws",
        target_service="payments",
        account_id="prod-123",
        region="us-east-1",
    )

    assert assessment.target_service == "payments"
    assert any("publicly exposed" in blocker for blocker in assessment.blockers)
    assert any("secrets backend" in recommendation for recommendation in assessment.recommendations)
    assert assessment.readiness_score < 0.92


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloud_specialist_inventory_task_returns_resources(
    cloud_agent: CloudSpecialistAgent,
) -> None:
    event = SwarmEvent(
        type=EventType.TASK_ASSIGNED,
        task_id="cloud-1",
        source_agent="captain",
        target_agent="cloud_specialist",
        payload={
            "action": "inventory_environment",
            "provider": "aws",
            "account_id": "prod-123",
            "region": "us-east-1",
        },
    )

    result = await cloud_agent.process_event(event)

    assert result is not None
    assert result.type == EventType.TASK_COMPLETED
    assert result.payload["summary"]["resource_count"] == 3
    assert len(result.payload["resources"]) == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloud_specialist_assessment_task_returns_blockers(
    cloud_agent: CloudSpecialistAgent,
) -> None:
    event = SwarmEvent(
        type=EventType.TASK_ASSIGNED,
        task_id="cloud-2",
        source_agent="captain",
        target_agent="cloud_specialist",
        payload={
            "action": "assess_deployment_target",
            "provider": "aws",
            "target_service": "payments",
            "account_id": "prod-123",
            "region": "us-east-1",
            "deployment_context": {"needs_kubernetes": True, "needs_registry": True},
        },
    )

    result = await cloud_agent.process_event(event)

    assert result is not None
    assert result.type == EventType.TASK_COMPLETED
    blockers = result.payload["assessment"]["blockers"]
    assert any("publicly exposed" in blocker for blocker in blockers)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloud_specialist_requires_valid_provider(
    cloud_agent: CloudSpecialistAgent,
) -> None:
    event = SwarmEvent(
        type=EventType.TASK_ASSIGNED,
        task_id="cloud-3",
        source_agent="captain",
        target_agent="cloud_specialist",
        payload={"action": "inventory_environment", "provider": "digitalocean"},
    )

    result = await cloud_agent.process_event(event)

    assert result is not None
    assert result.type == EventType.TASK_FAILED
    assert result.payload["error"] == "missing_or_invalid_provider"
