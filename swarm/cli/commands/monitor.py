from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import typer

from swarm.agents.watchman.agent import MonitoringSnapshot, WatchmanAgent
from swarm.cli.runtime import cli_settings, local_message_bus, run_async
from swarm.orchestrator.graph import build_swarm_graph
from swarm.orchestrator.state import SwarmState
from swarm.orchestrator.workflows.deploy_workflow import build_default_deploy_dependencies


def monitor(
    service: Annotated[str, typer.Argument()],
    namespace: Annotated[str, typer.Option("--namespace")] = "devops-swarm",
    snapshot_file: Annotated[
        Path | None,
        typer.Option(
            "--snapshot-file",
            help="Optional JSON snapshot used instead of live Prometheus/Loki queries.",
        ),
    ] = None,
    incident: Annotated[
        bool,
        typer.Option(
            "--incident",
            help="Escalate the monitoring snapshot into the incident workflow.",
        ),
    ] = False,
    sandbox_passed: Annotated[
        bool,
        typer.Option(
            "--sandbox-passed",
            help="Mark sandbox validation as already successful when escalating to incident mode.",
        ),
    ] = False,
    error_rate: Annotated[float | None, typer.Option("--error-rate")] = None,
    latency_p95_ms: Annotated[float | None, typer.Option("--latency-p95-ms")] = None,
    restart_count: Annotated[float | None, typer.Option("--restart-count")] = None,
    error_log_count: Annotated[int | None, typer.Option("--error-log-count")] = None,
) -> None:
    """Run a monitoring check or escalate a snapshot into incident triage."""

    settings = cli_settings()
    bus = local_message_bus(settings)
    snapshot = _load_or_collect_snapshot(
        settings=settings,
        bus=bus,
        service=service,
        namespace=namespace,
        snapshot_file=snapshot_file,
        error_rate=error_rate,
        latency_p95_ms=latency_p95_ms,
        restart_count=restart_count,
        error_log_count=error_log_count,
    )

    if not incident:
        typer.echo(f"Service: {snapshot.service}")
        typer.echo(f"Namespace: {snapshot.namespace}")
        typer.echo(f"Anomalies: {snapshot.anomalies or ['none']}")
        typer.echo(f"Error rate: {snapshot.error_rate:.3f}")
        typer.echo(f"Latency p95 ms: {snapshot.latency_p95_ms:.1f}")
        typer.echo(f"Restarts: {snapshot.restart_count:.1f}")
        typer.echo(f"Error logs: {snapshot.error_log_count}")
        return

    dependencies = build_default_deploy_dependencies(settings, bus)
    graph = build_swarm_graph(dependencies)
    state = SwarmState(
        task_id=f"incident-{uuid4().hex[:8]}",
        workflow_type="incident",
        alert_data=snapshot.model_dump(mode="json"),
        sandbox_test_passed=sandbox_passed,
    )
    result = run_async(graph.ainvoke(state))
    typer.echo(f"Task: {result.task_id}")
    typer.echo(f"Current step: {result.current_step}")
    typer.echo(f"Root cause hypothesis: {result.root_cause_hypothesis or 'unknown'}")
    typer.echo(f"Summary: {result.deployment_summary or 'incident triage complete'}")
    approval_id = result.alert_data.get("approval_request_id")
    if approval_id is not None:
        typer.echo(f"Approval request id: {approval_id}")


def _load_or_collect_snapshot(
    *,
    settings: Any,
    bus: Any,
    service: str,
    namespace: str,
    snapshot_file: Path | None,
    error_rate: float | None,
    latency_p95_ms: float | None,
    restart_count: float | None,
    error_log_count: int | None,
) -> MonitoringSnapshot:
    if snapshot_file is not None:
        raw = json.loads(snapshot_file.read_text(encoding="utf-8"))
        raw.setdefault("service", service)
        raw.setdefault("namespace", namespace)
        return MonitoringSnapshot.model_validate(raw)

    if any(
        value is not None
        for value in (error_rate, latency_p95_ms, restart_count, error_log_count)
    ):
        anomalies: list[str] = []
        normalized_error_rate = error_rate or 0.0
        normalized_latency = latency_p95_ms or 0.0
        normalized_restarts = restart_count or 0.0
        normalized_logs = error_log_count or 0
        if normalized_error_rate > 0.05:
            anomalies.append("high error rate")
        if normalized_latency > 750.0:
            anomalies.append("high latency")
        if normalized_restarts > 1.0:
            anomalies.append("elevated restarts")
        if normalized_logs > 3:
            anomalies.append("error logs detected")
        return MonitoringSnapshot(
            service=service,
            namespace=namespace,
            window_minutes=15,
            error_rate=normalized_error_rate,
            latency_p95_ms=normalized_latency,
            restart_count=normalized_restarts,
            error_log_count=normalized_logs,
            anomalies=anomalies,
            evidence=[f"CLI-provided snapshot for {service}."],
            confidence=0.9,
        )

    agent = WatchmanAgent(settings=settings, message_bus=bus)
    return run_async(agent.monitor_service(service=service, namespace=namespace))
