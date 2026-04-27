from __future__ import annotations

import structlog
from langgraph.graph import END, StateGraph

from forge.core.observability import observability_store
from forge.orchestrator.state import SwarmState
from forge.orchestrator.workflows.deploy_workflow import (
    DeployWorkflowDependencies,
    TypedStateWorkflow,
    build_deploy_workflow,
)
from forge.orchestrator.workflows.incident_workflow import build_incident_workflow


def build_swarm_graph(
    deploy_dependencies: DeployWorkflowDependencies,
) -> TypedStateWorkflow:
    """Build the master orchestration graph for supported workflows."""

    logger = structlog.get_logger().bind(component="swarm_orchestrator")
    deploy_graph = build_deploy_workflow(deploy_dependencies)
    incident_graph = build_incident_workflow(deploy_dependencies.captain_agent)

    async def workflow_router_node(state: SwarmState) -> SwarmState:
        state.current_step = "workflow_router"
        _log_state_transition(logger, "workflow_router", state)
        return state

    async def deploy_workflow_node(state: SwarmState) -> SwarmState:
        return await deploy_graph.ainvoke(state)

    async def incident_workflow_node(state: SwarmState) -> SwarmState:
        return await incident_graph.ainvoke(state)

    async def unsupported_workflow_node(state: SwarmState) -> SwarmState:
        state.current_step = "error"
        state.errors.append(f"unsupported_workflow_type: {state.workflow_type}")
        _log_state_transition(logger, "unsupported_workflow", state)
        return state

    def route_workflow(state: SwarmState) -> str:
        if state.workflow_type == "deploy":
            return "deploy_workflow"
        if state.workflow_type == "incident":
            return "incident_workflow"
        return "unsupported_workflow"

    graph = StateGraph(SwarmState)
    graph.add_node("workflow_router", workflow_router_node)
    graph.add_node("deploy_workflow", deploy_workflow_node)
    graph.add_node("incident_workflow", incident_workflow_node)
    graph.add_node("unsupported_workflow", unsupported_workflow_node)
    graph.set_entry_point("workflow_router")
    graph.add_conditional_edges(
        "workflow_router",
        route_workflow,
        {
            "deploy_workflow": "deploy_workflow",
            "incident_workflow": "incident_workflow",
            "unsupported_workflow": "unsupported_workflow",
        },
    )
    graph.add_edge("deploy_workflow", END)
    graph.add_edge("incident_workflow", END)
    graph.add_edge("unsupported_workflow", END)
    return TypedStateWorkflow(graph.compile())


def _log_state_transition(
    logger: structlog.stdlib.BoundLogger,
    node: str,
    state: SwarmState,
) -> None:
    observability_store.record_state(state)
    logger.info(
        "state_transition",
        node=node,
        task_id=state.task_id,
        state_snapshot=state.model_dump(mode="json"),
    )
