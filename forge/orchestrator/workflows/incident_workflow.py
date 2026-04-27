from __future__ import annotations

from typing import Literal

import structlog
from langgraph.graph import END, StateGraph

from forge.agents.captain.agent import CaptainAgent
from forge.agents.remediation.agent import RemediationAgent
from forge.agents.remediation.fix_evaluator import FixEvaluation, FixProposal, RootCauseHypothesis
from forge.core.approvals import approval_store
from forge.core.checkpoints import CheckpointRecord, CheckpointStore
from forge.core.observability import observability_store
from forge.orchestrator.state import AgentResult, SwarmState
from forge.orchestrator.workflows.deploy_workflow import TypedStateWorkflow


def build_incident_workflow(captain_agent: CaptainAgent) -> TypedStateWorkflow:
    """Build the Phase 2 incident remediation workflow."""

    logger = structlog.get_logger().bind(component="incident_workflow")
    remediation_agent = RemediationAgent(captain_agent.settings, captain_agent.bus)
    checkpoint_store = CheckpointStore(captain_agent.settings)

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

    async def evidence_collection_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="evidence_collection", task_id=state.task_id)
        evidence = await remediation_agent.collect_evidence(alert_data=state.alert_data)
        hypothesis = await remediation_agent.hypothesize_root_cause(
            alert_data=state.alert_data,
            evidence=evidence,
        )
        state.root_cause_hypothesis = hypothesis.summary
        state.alert_data["evidence_items"] = [item.model_dump(mode="json") for item in evidence]
        state.alert_data["root_cause_hypothesis"] = hypothesis.model_dump(mode="json")
        state.current_step = "incident_evidence_collected"
        _append_completed_step(state, "evidence_collection")
        _log_state_transition(node_logger, state)
        return state

    async def remediation_planning_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="remediation_planning", task_id=state.task_id)
        hypothesis = RootCauseHypothesis.model_validate(state.alert_data["root_cause_hypothesis"])
        proposal = await remediation_agent.propose_fix(
            alert_data=state.alert_data,
            hypothesis=hypothesis,
        )
        evaluation = await remediation_agent.evaluate_fix(proposal)
        state.fix_diff = proposal.change_plan
        state.alert_data["fix_proposal"] = proposal.model_dump(mode="json")
        state.alert_data["fix_evaluation"] = evaluation.model_dump(mode="json")
        state.current_step = "incident_fix_planned"
        _append_completed_step(state, "remediation_planning")
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

    async def reinvestigation_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="reinvestigation", task_id=state.task_id)
        retries = state.step_iterations.get("incident_reinvestigation", 0) + 1
        state.step_iterations["incident_reinvestigation"] = retries
        state.current_step = "reinvestigating"
        _log_state_transition(node_logger, state)
        return state

    async def sandbox_validation_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="sandbox_validation", task_id=state.task_id)
        proposal = FixProposal.model_validate(state.alert_data["fix_proposal"])
        evaluation = FixEvaluation.model_validate(state.alert_data["fix_evaluation"])
        raw_decision_data = state.alert_data.get("incident_decision", {})
        decision_data = raw_decision_data if isinstance(raw_decision_data, dict) else {}
        severity = str(decision_data.get("severity", "high"))
        state.sandbox_test_passed = evaluation.safe_for_sandbox
        if evaluation.safe_for_sandbox and severity in {"low", "medium"}:
            state.requires_human_approval = False
        if proposal.strategy == "observe":
            state.sandbox_test_passed = False
            state.current_step = "incident_observation_complete"
        else:
            state.current_step = "sandbox_validated"
        _append_completed_step(state, "sandbox_validation")
        _log_state_transition(node_logger, state)
        return state

    async def approval_request_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="approval_request", task_id=state.task_id)
        proposal = FixProposal.model_validate(state.alert_data["fix_proposal"])
        raw_decision_data = state.alert_data.get("incident_decision", {})
        decision_data = raw_decision_data if isinstance(raw_decision_data, dict) else {}
        request = approval_store.create_request(
            task_id=state.task_id,
            workflow_type=state.workflow_type,
            severity=_approval_severity(decision_data.get("severity")),
            summary=f"Approval required for incident task {state.task_id}",
            reason=str(decision_data.get("reason", proposal.summary)),
            proposed_action=proposal.summary,
            evidence=[item.summary for item in proposal.evidence],
        )
        await checkpoint_store.save(
            CheckpointRecord(
                task_id=state.task_id,
                workflow_type="incident",
                current_step="awaiting_approval",
                approval_request_id=request.id,
                state={
                    "task_id": state.task_id,
                    "fix_proposal": proposal.model_dump(mode="json"),
                    "alert_data": state.alert_data,
                },
            )
        )
        state.approval_status = "pending"
        state.requires_human_approval = True
        state.alert_data["approval_request_id"] = request.id
        state.current_step = "approval_requested"
        _append_completed_step(state, "approval_requested")
        _log_state_transition(node_logger, state)
        return state

    async def resolution_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="resolution", task_id=state.task_id)
        state.approval_status = None
        if state.current_step != "incident_observation_complete":
            state.current_step = "incident_resolved"
        _append_completed_step(state, "incident_resolved")
        _log_state_transition(node_logger, state)
        return state

    async def error_state_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="error_state", task_id=state.task_id)
        state.current_step = "error"
        _log_state_transition(node_logger, state)
        return state

    def route_after_triage(state: SwarmState) -> str:
        return "evidence_collection" if state.current_step != "error" else "error_state"

    def route_after_investigation(state: SwarmState) -> str:
        result = state.agent_results.get("captain")
        if result is None or not result.success:
            return "error_state"
        if (
            result.confidence < 0.70
            and state.step_iterations.get("incident_reinvestigation", 0) < 2
        ):
            return "reinvestigation"
        return "sandbox_validation"

    def route_after_sandbox(state: SwarmState) -> str:
        if state.current_step == "error":
            return "error_state"
        if state.requires_human_approval:
            return "approval_request"
        return "resolution"

    graph = StateGraph(SwarmState)
    graph.add_node("alert_triage", alert_triage_node)
    graph.add_node("evidence_collection", evidence_collection_node)
    graph.add_node("remediation_planning", remediation_planning_node)
    graph.add_node("captain_investigation", captain_investigation_node)
    graph.add_node("reinvestigation", reinvestigation_node)
    graph.add_node("sandbox_validation", sandbox_validation_node)
    graph.add_node("approval_request", approval_request_node)
    graph.add_node("resolution", resolution_node)
    graph.add_node("error_state", error_state_node)
    graph.set_entry_point("alert_triage")
    graph.add_conditional_edges(
        "alert_triage",
        route_after_triage,
        {
            "evidence_collection": "evidence_collection",
            "error_state": "error_state",
        },
    )
    graph.add_edge("evidence_collection", "remediation_planning")
    graph.add_edge("remediation_planning", "captain_investigation")
    graph.add_conditional_edges(
        "captain_investigation",
        route_after_investigation,
        {
            "reinvestigation": "reinvestigation",
            "sandbox_validation": "sandbox_validation",
            "error_state": "error_state",
        },
    )
    graph.add_edge("reinvestigation", "evidence_collection")
    graph.add_conditional_edges(
        "sandbox_validation",
        route_after_sandbox,
        {
            "approval_request": "approval_request",
            "resolution": "resolution",
            "error_state": "error_state",
        },
    )
    graph.add_edge("approval_request", END)
    graph.add_edge("resolution", END)
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
