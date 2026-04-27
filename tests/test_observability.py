from __future__ import annotations

from swarm.core.approvals import approval_store
from swarm.core.observability import observability_store
from swarm.orchestrator.state import AgentResult, SwarmState


def test_observability_store_summarizes_runs_and_pending_approvals() -> None:
    observability_store.reset()
    approval_store.reset()
    approval_store.create_request(
        task_id="incident-1",
        workflow_type="incident",
        severity="high",
        summary="Approval needed",
        reason="High severity incident",
        proposed_action="Rollback deployment",
        evidence=["Error rate is elevated."],
    )
    deploy_state = SwarmState(
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
    incident_state = SwarmState(
        task_id="incident-1",
        workflow_type="incident",
        current_step="approval_requested",
        requires_human_approval=True,
        approval_status="pending",
        errors=["high error rate detected"],
        agent_results={
            "captain": AgentResult(
                agent="captain",
                success=True,
                confidence=0.88,
            )
        },
    )

    observability_store.record_state(deploy_state)
    observability_store.record_state(incident_state)
    summary = observability_store.summary()

    assert summary.total_runs == 2
    assert summary.runs_by_workflow["deploy"] == 1
    assert summary.runs_by_workflow["incident"] == 1
    assert summary.pending_approvals == 1
    assert summary.runs_in_error == 1
    assert summary.average_agent_confidence > 0.85
    assert summary.recent_tasks[0].task_id in {"deploy-1", "incident-1"}
