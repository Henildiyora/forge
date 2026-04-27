from __future__ import annotations

import pytest

from forge.agents.captain.agent import CaptainAgent
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus
from forge.orchestrator.state import AgentResult, SwarmState
from tests.conftest import FakeRedisStreamClient


@pytest.fixture
def captain_agent(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> CaptainAgent:
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    return CaptainAgent(settings=test_settings, message_bus=bus)


@pytest.mark.unit
def test_captain_completes_when_artifacts_and_confidence_are_ready(
    captain_agent: CaptainAgent,
) -> None:
    state = SwarmState(
        task_id="deploy-1",
        workflow_type="deploy",
        project_metadata={"framework": "fastapi"},
        dockerfile="FROM python:3.11",
        k8s_manifests={"deployment.yaml": "apiVersion: apps/v1"},
        cicd_pipeline="name: ci",
        step_iterations={"config_generation": 1},
        agent_results={
            "docker_specialist": AgentResult(
                agent="docker_specialist",
                success=True,
                confidence=0.92,
            ),
            "k8s_specialist": AgentResult(
                agent="k8s_specialist",
                success=True,
                confidence=0.91,
            ),
            "cicd_specialist": AgentResult(
                agent="cicd_specialist",
                success=True,
                confidence=0.9,
            ),
        },
    )

    decision = captain_agent.review_deployment_state(state)

    assert decision.next_action == "complete"
    assert decision.confidence >= 0.9


@pytest.mark.unit
def test_captain_requests_retry_for_low_confidence(
    captain_agent: CaptainAgent,
) -> None:
    state = SwarmState(
        task_id="deploy-2",
        workflow_type="deploy",
        project_metadata={"framework": "express"},
        dockerfile="FROM node:20",
        k8s_manifests={"deployment.yaml": "apiVersion: apps/v1"},
        cicd_pipeline="name: ci",
        step_iterations={"config_generation": 1},
        agent_results={
            "docker_specialist": AgentResult(
                agent="docker_specialist",
                success=True,
                confidence=0.65,
            ),
            "k8s_specialist": AgentResult(
                agent="k8s_specialist",
                success=True,
                confidence=0.8,
            ),
            "cicd_specialist": AgentResult(
                agent="cicd_specialist",
                success=True,
                confidence=0.78,
            ),
        },
    )

    decision = captain_agent.review_deployment_state(state)

    assert decision.next_action == "retry_generation"
    assert "threshold" in decision.reason.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_captain_translates_scan_complete_event(
    captain_agent: CaptainAgent,
) -> None:
    event = SwarmEvent(
        type=EventType.CODEBASE_SCAN_COMPLETED,
        task_id="deploy-3",
        source_agent="librarian",
        target_agent="captain",
        payload={"framework": "fastapi"},
    )

    result = await captain_agent.process_event(event)

    assert result is not None
    assert result.type == EventType.DEPLOYMENT_PLAN_REQUESTED
    assert result.payload["scan_result"]["framework"] == "fastapi"


@pytest.mark.unit
def test_captain_requests_approval_for_high_severity_incident(
    captain_agent: CaptainAgent,
) -> None:
    state = SwarmState(
        task_id="incident-1",
        workflow_type="incident",
        alert_data={
            "service": "api",
            "anomalies": ["high errors", "high latency"],
            "error_rate": 0.12,
            "latency_p95_ms": 980.0,
            "restart_count": 2.0,
            "error_log_count": 5,
        },
        sandbox_test_passed=False,
    )

    decision = captain_agent.review_incident_state(state)

    assert decision.next_action == "request_approval"
    assert decision.severity in {"high", "critical"}
    assert decision.requires_human_approval is True
    assert "api" in decision.root_cause_hypothesis


@pytest.mark.unit
def test_captain_observes_low_severity_incident_when_sandbox_is_green(
    captain_agent: CaptainAgent,
) -> None:
    state = SwarmState(
        task_id="incident-2",
        workflow_type="incident",
        alert_data={
            "service": "api",
            "anomalies": [],
            "error_rate": 0.0,
            "latency_p95_ms": 120.0,
            "restart_count": 0.0,
            "error_log_count": 0,
        },
        sandbox_test_passed=True,
    )

    decision = captain_agent.review_incident_state(state)

    assert decision.next_action == "observe"
    assert decision.severity == "low"
    assert decision.requires_human_approval is False


@pytest.mark.unit
def test_captain_requests_retry_for_port_mismatch(
    captain_agent: CaptainAgent,
) -> None:
    state = SwarmState(
        task_id="deploy-4",
        workflow_type="deploy",
        project_metadata={"framework": "fastapi", "port": 8000, "env_vars": ["DATABASE_URL"]},
        dockerfile="FROM python:3.11\nEXPOSE 9000\n",
        docker_compose='services:\n  app:\n    ports:\n      - "8000:8000"\n',
        k8s_manifests={
            "deployment.yaml": "ports:\n- containerPort: 9000\n",
            "configmap.yaml": "data:\n  DATABASE_URL: postgres://db\n",
        },
        cicd_pipeline="name: ci",
        step_iterations={"config_generation": 1},
        agent_results={
            "docker_specialist": AgentResult(
                agent="docker_specialist",
                success=True,
                confidence=0.92,
            ),
            "k8s_specialist": AgentResult(
                agent="k8s_specialist",
                success=True,
                confidence=0.91,
            ),
            "cicd_specialist": AgentResult(
                agent="cicd_specialist",
                success=True,
                confidence=0.9,
            ),
        },
    )

    decision = captain_agent.review_deployment_state(state)

    assert decision.next_action == "retry_generation"
    assert "inconsistent" in decision.reason.lower()
    assert any("9000" in evidence for evidence in decision.evidence)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_captain_translates_anomaly_into_investigation_request(
    captain_agent: CaptainAgent,
) -> None:
    event = SwarmEvent(
        type=EventType.ANOMALY_DETECTED,
        task_id="incident-3",
        source_agent="watchman",
        target_agent="captain",
        payload={"service": "api", "anomalies": ["high latency"]},
    )

    result = await captain_agent.process_event(event)

    assert result is not None
    assert result.type == EventType.INVESTIGATION_REQUESTED
    assert result.payload["alert_data"]["service"] == "api"
