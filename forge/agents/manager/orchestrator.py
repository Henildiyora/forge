"""Manager-led specialist dispatch + Captain review for forge build."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from forge.agents.captain.agent import CaptainAgent
from forge.agents.captain.graph import build_captain_graph
from forge.agents.cicd_specialist.agent import CICDSpecialistAgent
from forge.agents.docker_specialist.agent import DockerSpecialistAgent
from forge.agents.k8s_specialist.agent import K8sSpecialistAgent
from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus
from forge.core.strategies import DeploymentStrategy
from forge.orchestrator.state import SwarmState
from forge.orchestrator.workflows.deploy_workflow import (
    CICDGenerationResult,
    DockerGenerationResult,
    KubernetesGenerationResult,
    _apply_cicd_result,
    _apply_docker_result,
    _apply_k8s_result,
)


async def publish_plan_event(
    bus: MessageBus,
    *,
    task_id: str,
    scan: CodebaseScanResult,
    target_agent: str,
) -> SwarmEvent:
    """Publish DEPLOYMENT_PLAN_REQUESTED for audit; returns the event for in-process dispatch."""

    event = SwarmEvent(
        type=EventType.DEPLOYMENT_PLAN_REQUESTED,
        task_id=task_id,
        source_agent="manager",
        target_agent=target_agent,
        payload={"scan_result": scan.model_dump(mode="json")},
    )
    await bus.publish(event)
    return event


async def run_manager_build_pipeline(
    *,
    settings: Settings,
    message_bus: MessageBus,
    project_path: str | Path,
    scan: CodebaseScanResult,
    strategy: DeploymentStrategy,
) -> SwarmState:
    """Dispatch specialists (in-process ``process_event``), publish to bus, Captain review."""

    task_id = f"build-{uuid4().hex[:8]}"
    meta = dict(scan.model_dump(mode="json"))
    meta["forge_strategy"] = strategy.value
    state = SwarmState(
        task_id=task_id,
        workflow_type="deploy",
        project_path=str(Path(project_path).expanduser().resolve()),
        project_metadata=meta,
        step_iterations={"config_generation": 1},
    )

    docker_agent = DockerSpecialistAgent(settings=settings, message_bus=message_bus)
    k8s_agent = K8sSpecialistAgent(settings=settings, message_bus=message_bus)
    cicd_agent = CICDSpecialistAgent(settings=settings, message_bus=message_bus)

    async def _run_docker() -> None:
        ev = await publish_plan_event(
            message_bus, task_id=task_id, scan=scan, target_agent=docker_agent.agent_name
        )
        out = await docker_agent.process_event(ev)
        if out is None or out.type == EventType.TASK_FAILED:
            raise RuntimeError("docker_specialist failed")
        _apply_docker_result(state, DockerGenerationResult.model_validate(out.payload))

    async def _run_k8s() -> None:
        ev = await publish_plan_event(
            message_bus, task_id=task_id, scan=scan, target_agent=k8s_agent.agent_name
        )
        out = await k8s_agent.process_event(ev)
        if out is None or out.type == EventType.TASK_FAILED:
            raise RuntimeError("k8s_specialist failed")
        _apply_k8s_result(state, KubernetesGenerationResult.model_validate(out.payload))

    async def _run_cicd() -> None:
        ev = await publish_plan_event(
            message_bus, task_id=task_id, scan=scan, target_agent=cicd_agent.agent_name
        )
        out = await cicd_agent.process_event(ev)
        if out is None or out.type == EventType.TASK_FAILED:
            raise RuntimeError("cicd_specialist failed")
        _apply_cicd_result(state, CICDGenerationResult.model_validate(out.payload))

    if strategy == DeploymentStrategy.DOCKER_COMPOSE:
        await _run_docker()
    elif strategy == DeploymentStrategy.CICD_ONLY:
        await _run_cicd()
    elif strategy == DeploymentStrategy.KUBERNETES:
        await asyncio.gather(_run_docker(), _run_k8s(), _run_cicd())
    else:
        await asyncio.gather(_run_docker(), _run_k8s(), _run_cicd())

    captain = CaptainAgent(settings=settings, message_bus=message_bus)
    graph = build_captain_graph(captain)
    raw_final = await graph.ainvoke(state)
    if isinstance(raw_final, SwarmState):
        return raw_final
    return SwarmState.model_validate(raw_final)
