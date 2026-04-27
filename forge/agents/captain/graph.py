from __future__ import annotations

import structlog
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from forge.agents.captain.agent import CaptainAgent, CaptainDecision
from forge.orchestrator.state import SwarmState


def build_captain_graph(captain_agent: CaptainAgent) -> CompiledStateGraph:
    """Build the Captain decision graph used by orchestration workflows."""

    logger = structlog.get_logger().bind(component="captain_graph")

    async def review_state_node(state: SwarmState) -> SwarmState:
        decision = captain_agent.review_deployment_state(state)
        state.decision_log.append(decision.model_dump(mode="json"))
        state.current_step = "captain_review"
        _append_completed_step(state, "captain_review")
        _log_state_transition(logger, "captain_review", state)
        return state

    async def retry_generation_node(state: SwarmState) -> SwarmState:
        decision = _latest_decision(state)
        state.iteration_count += 1
        state.current_step = "retry_config_generation"
        state.errors.append(f"captain_retry: {decision.reason}")
        _log_state_transition(logger, "retry_generation", state)
        return state

    async def complete_plan_node(state: SwarmState) -> SwarmState:
        decision = _latest_decision(state)
        state.current_step = "deployment_plan_ready"
        state.deployment_summary = decision.reason
        _append_completed_step(state, "deployment_plan_ready")
        _log_state_transition(logger, "complete_plan", state)
        return state

    async def halt_workflow_node(state: SwarmState) -> SwarmState:
        decision = _latest_decision(state)
        state.current_step = "error"
        state.errors.append(decision.reason)
        _log_state_transition(logger, "halt_workflow", state)
        return state

    def route_after_review(state: SwarmState) -> str:
        return _latest_decision(state).next_action

    graph = StateGraph(SwarmState)
    graph.add_node("captain_review", review_state_node)
    graph.add_node("retry_generation", retry_generation_node)
    graph.add_node("complete", complete_plan_node)
    graph.add_node("halt", halt_workflow_node)
    graph.set_entry_point("captain_review")
    graph.add_conditional_edges(
        "captain_review",
        route_after_review,
        {
            "retry_generation": "retry_generation",
            "complete": "complete",
            "halt": "halt",
        },
    )
    graph.add_edge("retry_generation", END)
    graph.add_edge("complete", END)
    graph.add_edge("halt", END)
    return graph.compile()


def _latest_decision(state: SwarmState) -> CaptainDecision:
    return CaptainDecision.model_validate(state.decision_log[-1])


def _append_completed_step(state: SwarmState, step: str) -> None:
    if step not in state.completed_steps:
        state.completed_steps.append(step)


def _log_state_transition(
    logger: structlog.stdlib.BoundLogger,
    node_name: str,
    state: SwarmState,
) -> None:
    logger.info(
        "state_transition",
        node=node_name,
        task_id=state.task_id,
        current_step=state.current_step,
        state_snapshot=state.model_dump(mode="json"),
    )
