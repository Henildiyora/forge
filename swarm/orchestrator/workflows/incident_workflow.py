from __future__ import annotations

from typing import Literal

import structlog
from langgraph.graph import END, StateGraph

from swarm.agents.captain.agent import CaptainAgent
from swarm.core.approvals import approval_store
from swarm.core.observability import observability_store
from swarm.orchestrator.state import AgentResult, SwarmState
from swarm.orchestrator.workflows.deploy_workflow import TypedStateWorkflow


def build_incident_workflow(captain_agent: CaptainAgent) -> TypedStateWorkflow:
    """Build the incident-response workflow used by the master orchestrator."""

    logger = structlog.get_logger().bind(component="incident_workflow")

    async def alert_triage_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="alert_triage", task_id=state.task_id)
        if not state.alert_data:
            state.errors.append("incident workflow requires alert_data")
            state.current_step = "error"
        else:
            state.current_step = "alert_triaged"
            _append_completed_step(state, "alert_triage")
        _log_state_transition(node_logger, state)
        return state

    async def captain_investigation_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="captain_investigation", task_id=state.task_id)
        decision = captain_agent.review_incident_state(state)
        state.root_cause_hypothesis = decision.root_cause_hypothesis
        state.fix_diff = decision.proposed_action
        state.requires_human_approval = decision.requires_human_approval
        state.deployment_summary = decision.reason
        state.decision_log.append(
            {
                "agent": "captain",
                "decision_type": "incident",
                **decision.model_dump(mode="json"),
            }
        )
        state.agent_results["captain"] = AgentResult(
            agent="captain",
            success=decision.next_action != "halt",
            data=decision.model_dump(mode="json"),
            evidence=decision.evidence,
            confidence=decision.confidence,
        )
        state.alert_data["incident_decision"] = decision.model_dump(mode="json")
        state.current_step = "incident_investigated"
        _append_completed_step(state, "captain_investigation")
        _log_state_transition(node_logger, state)
        return state

    async def approval_request_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="approval_request", task_id=state.task_id)
        decision_data = state.alert_data.get("incident_decision", {})
        if not isinstance(decision_data, dict):
            state.errors.append("incident decision is missing from alert_data")
            state.current_step = "error"
            _log_state_transition(node_logger, state)
            return state

        request = approval_store.create_request(
            task_id=state.task_id,
            workflow_type=state.workflow_type,
            severity=_approval_severity(decision_data.get("severity")),
            summary=f"Approval required for incident task {state.task_id}",
            reason=str(decision_data.get("reason", "Incident requires review.")),
            proposed_action=str(decision_data.get("proposed_action", "Review proposed action.")),
            evidence=[
                evidence
                for evidence in decision_data.get("evidence", [])
                if isinstance(evidence, str)
            ],
        )
        state.approval_status = "pending"
        state.requires_human_approval = True
        state.alert_data["approval_request_id"] = request.id
        state.current_step = "approval_requested"
        _append_completed_step(state, "approval_requested")
        _log_state_transition(node_logger, state)
        return state

    async def observation_complete_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="observation_complete", task_id=state.task_id)
        state.approval_status = None
        state.current_step = "incident_observation_complete"
        _append_completed_step(state, "incident_observation_complete")
        _log_state_transition(node_logger, state)
        return state

    async def error_state_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="error_state", task_id=state.task_id)
        state.current_step = "error"
        _log_state_transition(node_logger, state)
        return state

    def route_after_triage(state: SwarmState) -> str:
        return "captain_investigation" if state.current_step != "error" else "error_state"

    def route_after_investigation(state: SwarmState) -> str:
        result = state.agent_results.get("captain")
        if result is None or not result.success:
            return "error_state"
        decision_data = result.data
        next_action = decision_data.get("next_action")
        if next_action == "request_approval":
            return "approval_request"
        if next_action == "observe":
            return "observation_complete"
        return "error_state"

    graph = StateGraph(SwarmState)
    graph.add_node("alert_triage", alert_triage_node)
    graph.add_node("captain_investigation", captain_investigation_node)
    graph.add_node("approval_request", approval_request_node)
    graph.add_node("observation_complete", observation_complete_node)
    graph.add_node("error_state", error_state_node)
    graph.set_entry_point("alert_triage")
    graph.add_conditional_edges(
        "alert_triage",
        route_after_triage,
        {
            "captain_investigation": "captain_investigation",
            "error_state": "error_state",
        },
    )
    graph.add_conditional_edges(
        "captain_investigation",
        route_after_investigation,
        {
            "approval_request": "approval_request",
            "observation_complete": "observation_complete",
            "error_state": "error_state",
        },
    )
    graph.add_edge("approval_request", END)
    graph.add_edge("observation_complete", END)
    graph.add_edge("error_state", END)
    return TypedStateWorkflow(graph.compile())


def _append_completed_step(state: SwarmState, step: str) -> None:
    if step not in state.completed_steps:
        state.completed_steps.append(step)


def _log_state_transition(
    logger: structlog.stdlib.BoundLogger,
    state: SwarmState,
) -> None:
    observability_store.record_state(state)
    logger.info(
        "state_transition",
        current_step=state.current_step,
        state_snapshot=state.model_dump(mode="json"),
    )


def _approval_severity(value: object) -> Literal["low", "medium", "high", "critical"]:
    if value == "low":
        return "low"
    if value == "medium":
        return "medium"
    if value == "high":
        return "high"
    if value == "critical":
        return "critical"
    return "high"
