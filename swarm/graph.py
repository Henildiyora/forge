"""LangGraph wiring for the three-agent swarm plus dry-run gate.

This is a real ``StateGraph`` with two ``add_conditional_edges`` calls:

1. After monitoring: anomaly → code analysis; otherwise → no_incident.
2. After dry-run: pass → ready_to_apply; fail with repair budget → ops again;
   fail with budget spent → needs_human_review.

The repair edge is capped by ``SwarmState.repair_attempts`` so a failing fix
is never retried blindly forever.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from swarm.progress import NullRecorder, RunRecorder
from swarm.runtime import SwarmRuntime
from swarm.schemas import RunStatus, SwarmState


def build_swarm_graph(
    runtime: SwarmRuntime,
    recorder: RunRecorder | None = None,
) -> Any:
    """Compile the incident triage graph bound to a concrete runtime."""

    rec = recorder or NullRecorder()

    def monitoring_node(state: SwarmState) -> SwarmState:
        return _run_node(state, "monitoring_agent", rec, _monitoring, runtime)

    def code_analysis_node(state: SwarmState) -> SwarmState:
        return _run_node(state, "code_analysis_agent", rec, _code_analysis, runtime)

    def ops_node(state: SwarmState) -> SwarmState:
        return _run_node(state, "ops_agent", rec, _ops, runtime)

    def dry_run_node(state: SwarmState) -> SwarmState:
        return _run_node(state, "dry_run_validate", rec, _dry_run, runtime)

    def no_incident_node(state: SwarmState) -> SwarmState:
        state.current_node = "no_incident"
        state.status = RunStatus.NO_INCIDENT
        state.finished_at = datetime.now(UTC)
        if "no_incident" not in state.completed_nodes:
            state.completed_nodes.append("no_incident")
        rec.node_started("no_incident")
        rec.node_finished("no_incident", 0.0, {"status": state.status.value})
        return state

    def ready_node(state: SwarmState) -> SwarmState:
        state.current_node = "ready_to_apply"
        state.status = RunStatus.READY_TO_APPLY
        state.finished_at = datetime.now(UTC)
        if "ready_to_apply" not in state.completed_nodes:
            state.completed_nodes.append("ready_to_apply")
        rec.node_started("ready_to_apply")
        rec.node_finished(
            "ready_to_apply",
            0.0,
            {"status": state.status.value, "fix": state.proposed_fix},
        )
        return state

    def human_review_node(state: SwarmState) -> SwarmState:
        state.current_node = "needs_human_review"
        state.status = RunStatus.NEEDS_HUMAN_REVIEW
        latest = state.latest_dry_run
        state.human_review_reason = (
            latest.rejection_reason
            if latest is not None
            else "dry-run failed with no result"
        )
        state.finished_at = datetime.now(UTC)
        if "needs_human_review" not in state.completed_nodes:
            state.completed_nodes.append("needs_human_review")
        rec.node_started("needs_human_review")
        rec.node_finished(
            "needs_human_review",
            0.0,
            {
                "status": state.status.value,
                "reason": state.human_review_reason,
                "logs": latest.logs if latest else "",
            },
        )
        return state

    def route_after_monitoring(
        state: SwarmState,
    ) -> Literal["code_analysis_agent", "no_incident"]:
        if state.incident is not None:
            return "code_analysis_agent"
        return "no_incident"

    def route_after_dry_run(
        state: SwarmState,
    ) -> Literal["ready_to_apply", "ops_agent", "needs_human_review"]:
        latest = state.latest_dry_run
        if latest is not None and latest.passed:
            return "ready_to_apply"
        # Budget already consumed when we incremented repair_attempts after failure.
        if state.repair_attempts < state.config.max_repair_attempts:
            return "ops_agent"
        return "needs_human_review"

    graph: StateGraph = StateGraph(SwarmState)
    graph.add_node("monitoring_agent", monitoring_node)
    graph.add_node("code_analysis_agent", code_analysis_node)
    graph.add_node("ops_agent", ops_node)
    graph.add_node("dry_run_validate", dry_run_node)
    graph.add_node("no_incident", no_incident_node)
    graph.add_node("ready_to_apply", ready_node)
    graph.add_node("needs_human_review", human_review_node)

    graph.set_entry_point("monitoring_agent")
    graph.add_conditional_edges(
        "monitoring_agent",
        route_after_monitoring,
        {
            "code_analysis_agent": "code_analysis_agent",
            "no_incident": "no_incident",
        },
    )
    graph.add_edge("code_analysis_agent", "ops_agent")
    graph.add_edge("ops_agent", "dry_run_validate")
    graph.add_conditional_edges(
        "dry_run_validate",
        route_after_dry_run,
        {
            "ready_to_apply": "ready_to_apply",
            "ops_agent": "ops_agent",
            "needs_human_review": "needs_human_review",
        },
    )
    graph.add_edge("no_incident", END)
    graph.add_edge("ready_to_apply", END)
    graph.add_edge("needs_human_review", END)
    return graph.compile()


def run_swarm(
    state: SwarmState,
    runtime: SwarmRuntime,
    recorder: RunRecorder | None = None,
) -> SwarmState:
    """Execute a full swarm run and return the final shared state."""

    rec = recorder or RunRecorder(state.run_id, runtime.settings.runs_dir)
    rec.emit("run_started", config=state.config.model_dump(mode="json"))
    graph = build_swarm_graph(runtime, rec)
    # LangGraph may return a dict depending on version; normalize to SwarmState.
    result = graph.invoke(state)
    if isinstance(result, SwarmState):
        final = result
    else:
        final = SwarmState.model_validate(result)
    if final.finished_at is None:
        final.finished_at = datetime.now(UTC)
    rec.emit(
        "run_finished",
        status=final.status.value,
        elapsed_seconds=final.elapsed_seconds,
        completed_nodes=final.completed_nodes,
    )
    return final


def _run_node(
    state: SwarmState,
    name: str,
    recorder: RunRecorder,
    fn: Any,
    runtime: SwarmRuntime,
) -> SwarmState:
    state.current_node = name
    recorder.node_started(name)
    started = time.perf_counter()
    try:
        payload = fn(state, runtime)
        duration = time.perf_counter() - started
        state.node_durations[name] = state.node_durations.get(name, 0.0) + duration
        if name not in state.completed_nodes:
            state.completed_nodes.append(name)
        recorder.node_finished(name, duration, payload)
    except Exception as exc:  # noqa: BLE001 - captured on state for human review
        duration = time.perf_counter() - started
        state.node_durations[name] = state.node_durations.get(name, 0.0) + duration
        state.errors.append(f"{name}: {exc}")
        state.status = RunStatus.ERROR
        state.finished_at = datetime.now(UTC)
        recorder.node_failed(name, str(exc))
        raise
    return state


def _monitoring(state: SwarmState, runtime: SwarmRuntime) -> Any:
    signal, calls, results = runtime.monitoring.run(state)
    state.incident = signal
    state.tool_calls.extend(calls)
    state.tool_results.extend(results)
    return {"incident": signal}


def _code_analysis(state: SwarmState, runtime: SwarmRuntime) -> Any:
    candidates, calls, results = runtime.code_analysis.run(state)
    state.commit_candidates = candidates
    state.tool_calls.extend(calls)
    state.tool_results.extend(results)
    return {"commit_candidates": candidates}


def _ops(state: SwarmState, runtime: SwarmRuntime) -> Any:
    # Re-entering after a failed dry-run consumes one repair attempt.
    if state.dry_run_results and not (state.latest_dry_run and state.latest_dry_run.passed):
        state.repair_attempts += 1
    fix = runtime.ops.run(state)
    state.proposed_fix = fix
    return {"proposed_fix": fix}


def _dry_run(state: SwarmState, runtime: SwarmRuntime) -> Any:
    if state.proposed_fix is None:
        raise RuntimeError("dry-run requires a ProposedFix in shared state")
    attempt = len(state.dry_run_results) + 1
    result = runtime.sandbox.validate(
        state.proposed_fix,
        state.config,
        attempt=attempt,
    )
    state.dry_run_results.append(result)
    return {"dry_run_result": result}
