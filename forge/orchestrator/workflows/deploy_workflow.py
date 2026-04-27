from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from langchain_core.runnables.graph import Graph as RunnableGraph
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from forge.agents.captain.agent import CaptainAgent
from forge.agents.captain.graph import build_captain_graph
from forge.agents.librarian.agent import LibrarianAgent
from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.core.config import Settings
from forge.core.message_bus import MessageBus
from forge.core.observability import observability_store
from forge.orchestrator.state import AgentResult, SwarmState


class DockerGenerationResult(BaseModel):
    """Result returned by the Docker specialist mock or implementation."""

    dockerfile: str = Field(description="Generated Dockerfile content.")
    docker_compose: str | None = Field(
        default=None,
        description="Generated docker-compose content when available.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting the generated Docker assets.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score assigned by the Docker specialist.",
    )


class KubernetesGenerationResult(BaseModel):
    """Result returned by the Kubernetes specialist mock or implementation."""

    manifests: dict[str, str] = Field(
        default_factory=dict,
        description="Generated Kubernetes manifests keyed by filename.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting the generated manifests.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score assigned by the Kubernetes specialist.",
    )


class CICDGenerationResult(BaseModel):
    """Result returned by the CI/CD specialist mock or implementation."""

    pipeline: str = Field(description="Generated CI/CD pipeline definition.")
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting the generated pipeline.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score assigned by the CI/CD specialist.",
    )


DockerGenerator = Callable[[CodebaseScanResult], Awaitable[DockerGenerationResult]]
KubernetesGenerator = Callable[[CodebaseScanResult], Awaitable[KubernetesGenerationResult]]
CICDGenerator = Callable[[CodebaseScanResult], Awaitable[CICDGenerationResult]]


@dataclass(frozen=True)
class DeployWorkflowDependencies:
    """Services required to execute the deploy workflow graph."""

    librarian_agent: LibrarianAgent
    captain_agent: CaptainAgent
    docker_generator: DockerGenerator
    k8s_generator: KubernetesGenerator
    cicd_generator: CICDGenerator


def build_default_deploy_dependencies(
    settings: Settings,
    message_bus: MessageBus,
) -> DeployWorkflowDependencies:
    """Create deploy workflow dependencies backed by real Sprint 4 specialists."""

    from forge.agents.cicd_specialist.agent import CICDSpecialistAgent
    from forge.agents.docker_specialist.agent import DockerSpecialistAgent
    from forge.agents.k8s_specialist.agent import K8sSpecialistAgent

    librarian = LibrarianAgent(settings=settings, message_bus=message_bus)
    captain = CaptainAgent(settings=settings, message_bus=message_bus)
    docker_specialist = DockerSpecialistAgent(settings=settings, message_bus=message_bus)
    k8s_specialist = K8sSpecialistAgent(settings=settings, message_bus=message_bus)
    cicd_specialist = CICDSpecialistAgent(settings=settings, message_bus=message_bus)
    return DeployWorkflowDependencies(
        librarian_agent=librarian,
        captain_agent=captain,
        docker_generator=docker_specialist.generate_artifacts,
        k8s_generator=k8s_specialist.generate_artifacts,
        cicd_generator=cicd_specialist.generate_artifacts,
    )


class TypedStateWorkflow:
    """Thin wrapper that normalizes LangGraph outputs back into SwarmState."""

    def __init__(self, compiled_graph: CompiledStateGraph):
        self._compiled_graph = compiled_graph

    async def ainvoke(self, state: SwarmState) -> SwarmState:
        return SwarmState.model_validate(await self._compiled_graph.ainvoke(state))

    def invoke(self, state: SwarmState) -> SwarmState:
        return SwarmState.model_validate(self._compiled_graph.invoke(state))

    def get_graph(self) -> RunnableGraph:
        return self._compiled_graph.get_graph()


def build_deploy_workflow(
    dependencies: DeployWorkflowDependencies,
) -> TypedStateWorkflow:
    """Build the deploy workflow graph used by the master orchestrator."""

    logger = structlog.get_logger().bind(component="deploy_workflow")
    captain_graph = build_captain_graph(dependencies.captain_agent)

    async def librarian_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="librarian_scan", task_id=state.task_id)
        try:
            if state.project_path is None:
                raise ValueError("project_path is required for deploy workflows")
            result = await dependencies.librarian_agent.analyze_codebase(state.project_path)
            state.agent_results["librarian"] = AgentResult(
                agent="librarian",
                success=True,
                data=result.model_dump(mode="json"),
                evidence=result.evidence,
                confidence=result.confidence,
            )
            state.project_metadata = result.model_dump(mode="json")
            state.current_step = "librarian_scan_completed"
            _append_completed_step(state, "librarian_scan")
        except Exception as exc:
            state.errors.append(f"librarian: {exc}")
            state.agent_results["librarian"] = AgentResult(
                agent="librarian",
                success=False,
                data={},
                evidence=[f"librarian failure: {exc}"],
                confidence=0.0,
            )
            state.current_step = "error"
        _log_state_transition(node_logger, state)
        return state

    async def config_generation_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="config_generation", task_id=state.task_id)
        attempt = state.step_iterations.get("config_generation", 0) + 1
        state.step_iterations["config_generation"] = attempt
        if attempt > state.max_iterations:
            state.errors.append("config_generation exceeded max iteration budget")
            state.current_step = "error"
            _log_state_transition(node_logger, state)
            return state

        scan_result = CodebaseScanResult.model_validate(state.project_metadata)
        results = await asyncio.gather(
            dependencies.docker_generator(scan_result),
            dependencies.k8s_generator(scan_result),
            dependencies.cicd_generator(scan_result),
            return_exceptions=True,
        )
        docker_result, k8s_result, cicd_result = results
        _apply_docker_result(state, docker_result)
        _apply_k8s_result(state, k8s_result)
        _apply_cicd_result(state, cicd_result)
        state.current_step = "config_generation_completed"
        config_agents = ("docker_specialist", "k8s_specialist", "cicd_specialist")
        if all(
            state.agent_results.get(
                agent_name,
                AgentResult(agent=agent_name, success=False),
            ).success
            for agent_name in config_agents
        ):
            _append_completed_step(state, "config_generation")
        _log_state_transition(node_logger, state)
        return state

    async def captain_review_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="captain_review", task_id=state.task_id)
        reviewed_state = SwarmState.model_validate(await captain_graph.ainvoke(state))
        _log_state_transition(node_logger, reviewed_state)
        return reviewed_state

    async def error_state_node(state: SwarmState) -> SwarmState:
        node_logger = logger.bind(node="error_state", task_id=state.task_id)
        state.current_step = "error"
        _log_state_transition(node_logger, state)
        return state

    def route_after_scan(state: SwarmState) -> str:
        result = state.agent_results.get("librarian")
        if result is None or not result.success:
            return "error_state"
        return "config_generation"

    def route_after_review(state: SwarmState) -> str:
        if state.current_step == "retry_config_generation":
            return "config_generation"
        if state.current_step == "deployment_plan_ready":
            return END
        return "error_state"

    graph = StateGraph(SwarmState)
    graph.add_node("librarian_scan", librarian_node)
    graph.add_node("config_generation", config_generation_node)
    graph.add_node("captain_review", captain_review_node)
    graph.add_node("error_state", error_state_node)
    graph.set_entry_point("librarian_scan")
    graph.add_conditional_edges(
        "librarian_scan",
        route_after_scan,
        {
            "config_generation": "config_generation",
            "error_state": "error_state",
        },
    )
    graph.add_edge("config_generation", "captain_review")
    graph.add_conditional_edges(
        "captain_review",
        route_after_review,
        {
            "config_generation": "config_generation",
            "error_state": "error_state",
            END: END,
        },
    )
    graph.add_edge("error_state", END)
    return TypedStateWorkflow(graph.compile())


def _apply_docker_result(
    state: SwarmState,
    result: DockerGenerationResult | BaseException,
) -> None:
    if isinstance(result, BaseException):
        state.errors.append(f"docker_specialist: {result}")
        state.agent_results["docker_specialist"] = AgentResult(
            agent="docker_specialist",
            success=False,
            data={},
            evidence=[str(result)],
            confidence=0.0,
        )
        return
    state.dockerfile = result.dockerfile
    state.docker_compose = result.docker_compose
    state.agent_results["docker_specialist"] = AgentResult(
        agent="docker_specialist",
        success=True,
        data={
            "dockerfile": result.dockerfile,
            "docker_compose": result.docker_compose,
        },
        evidence=result.evidence,
        confidence=result.confidence,
    )


def _apply_k8s_result(
    state: SwarmState,
    result: KubernetesGenerationResult | BaseException,
) -> None:
    if isinstance(result, BaseException):
        state.errors.append(f"k8s_specialist: {result}")
        state.agent_results["k8s_specialist"] = AgentResult(
            agent="k8s_specialist",
            success=False,
            data={},
            evidence=[str(result)],
            confidence=0.0,
        )
        return
    state.k8s_manifests = result.manifests
    state.agent_results["k8s_specialist"] = AgentResult(
        agent="k8s_specialist",
        success=True,
        data={"manifests": result.manifests},
        evidence=result.evidence,
        confidence=result.confidence,
    )


def _apply_cicd_result(
    state: SwarmState,
    result: CICDGenerationResult | BaseException,
) -> None:
    if isinstance(result, BaseException):
        state.errors.append(f"cicd_specialist: {result}")
        state.agent_results["cicd_specialist"] = AgentResult(
            agent="cicd_specialist",
            success=False,
            data={},
            evidence=[str(result)],
            confidence=0.0,
        )
        return
    state.cicd_pipeline = result.pipeline
    state.agent_results["cicd_specialist"] = AgentResult(
        agent="cicd_specialist",
        success=True,
        data={"pipeline": result.pipeline},
        evidence=result.evidence,
        confidence=result.confidence,
    )


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
