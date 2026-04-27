from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.api.dependencies import get_checkpoint_store
from forge.core.approvals import approval_store
from forge.core.checkpoints import CheckpointRecord
from forge.core.hardening import hardening_store
from forge.core.observability import observability_store
from forge.orchestrator.state import AgentResult, SwarmState


def test_health_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_capabilities_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/forge/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert "incident" in payload["workflows"]
    assert "sandbox_tester" in payload["agents"]
    assert "cloud_specialist" in payload["agents"]


def test_approval_endpoints_list_and_resolve_requests() -> None:
    approval_store.reset()
    request = approval_store.create_request(
        task_id="incident-1",
        workflow_type="incident",
        severity="high",
        summary="Approval needed",
        reason="High severity incident",
        proposed_action="Rollback deployment",
        evidence=["Error rate is elevated."],
    )
    client = TestClient(create_app())

    pending_response = client.get("/api/v1/approvals/pending")
    assert pending_response.status_code == 200
    assert pending_response.json()[0]["id"] == request.id

    grant_response = client.post(
        f"/api/v1/approvals/{request.id}/grant",
        json={"reviewer": "alice", "note": "approved"},
    )
    assert grant_response.status_code == 200
    assert grant_response.json()["status"] == "granted"

    all_response = client.get("/api/v1/approvals")
    assert all_response.status_code == 200
    assert all_response.json()[0]["reviewer"] == "alice"


def test_approval_page_and_task_resolution_endpoint() -> None:
    approval_store.reset()
    approval_store.create_request(
        task_id="incident-task-1",
        workflow_type="incident",
        severity="high",
        summary="Approval needed",
        reason="High severity incident",
        proposed_action="Rollback deployment",
        evidence=["Error rate is elevated."],
    )
    checkpoint_store = get_checkpoint_store()
    import asyncio

    asyncio.run(
        checkpoint_store.save(
            CheckpointRecord(
                task_id="incident-task-1",
                workflow_type="incident",
                current_step="awaiting_approval",
                state={
                    "fix_proposal": {
                        "strategy": "rollback",
                        "summary": "Rollback deployment",
                        "change_plan": "Undo the latest rollout.",
                        "undo_path": "Redeploy the bad revision if needed.",
                        "test_plan": "Watch error rate after rollback.",
                        "requires_human_approval": True,
                        "confidence": 0.9,
                        "evidence": [],
                        "deployment_name": "api",
                        "previous_revision": "1",
                    }
                },
            )
        )
    )
    client = TestClient(create_app())

    page_response = client.get("/api/v1/approvals/incident-task-1")
    assert page_response.status_code == 200
    assert "FORGE Approval" in page_response.text

    approve_response = client.post("/api/v1/approvals/incident-task-1/approve")
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"


def test_slack_webhook_can_resume_incident_checkpoint() -> None:
    approval_store.reset()
    approval_store.create_request(
        task_id="incident-task-2",
        workflow_type="incident",
        severity="high",
        summary="Approval needed",
        reason="High severity incident",
        proposed_action="Rollback deployment",
        evidence=["Error rate is elevated."],
    )
    checkpoint_store = get_checkpoint_store()
    import asyncio

    asyncio.run(
        checkpoint_store.save(
            CheckpointRecord(
                task_id="incident-task-2",
                workflow_type="incident",
                current_step="awaiting_approval",
                state={
                    "fix_proposal": {
                        "strategy": "rollback",
                        "summary": "Rollback deployment",
                        "change_plan": "Undo the latest rollout.",
                        "undo_path": "Redeploy the bad revision if needed.",
                        "test_plan": "Watch error rate after rollback.",
                        "requires_human_approval": True,
                        "confidence": 0.9,
                        "evidence": [],
                        "deployment_name": "api",
                        "previous_revision": "1",
                    }
                },
            )
        )
    )
    client = TestClient(create_app())
    payload = {
        "actions": [{"action_id": "approve_incident-task-2", "value": "incident-task-2"}]
    }

    response = client.post(
        "/api/v1/webhooks/slack/actions",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_observability_endpoint_returns_summary() -> None:
    approval_store.reset()
    observability_store.reset()
    approval_store.create_request(
        task_id="incident-1",
        workflow_type="incident",
        severity="high",
        summary="Approval needed",
        reason="High severity incident",
        proposed_action="Rollback deployment",
        evidence=["Error rate is elevated."],
    )
    observability_store.record_state(
        SwarmState(
            task_id="deploy-1",
            workflow_type="deploy",
            current_step="deployment_plan_ready",
            sandbox_test_passed=True,
            agent_results={
                "docker_specialist": AgentResult(
                    agent="docker_specialist",
                    success=True,
                    confidence=0.9,
                )
            },
        )
    )
    client = TestClient(create_app())

    response = client.get("/api/v1/forge/observability")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_runs"] >= 1
    assert payload["pending_approvals"] == 1
    assert "deploy" in payload["runs_by_workflow"]


def test_hardening_endpoints_run_and_return_latest(
    python_fastapi_project: Path,
) -> None:
    hardening_store.reset()
    client = TestClient(create_app())

    run_response = client.post(
        "/api/v1/forge/hardening/run",
        json={"project_path": str(python_fastapi_project), "max_iterations": 3},
    )

    assert run_response.status_code == 200
    payload = run_response.json()
    assert payload["total_scenarios"] == 5
    assert payload["failed_scenarios"] == 0
    assert payload["observability"]["total_runs"] >= 4

    latest_response = client.get("/api/v1/forge/hardening/latest")

    assert latest_response.status_code == 200
    latest_payload = latest_response.json()
    assert latest_payload is not None
    assert latest_payload["project_path"] == str(python_fastapi_project)
