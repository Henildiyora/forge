"""Shared Pydantic contracts for the DevOps Swarm.

Every agent reads and writes these models through :class:`SwarmState`, and every
tool invocation is wrapped in the :class:`ToolCall` / :class:`ToolResult`
envelope. That is what makes agent outputs composable instead of three
unrelated blobs of text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------
# Standardized tool-calling envelope
# --------------------------------------------------------------------------


class ToolCall(BaseModel):
    """A single tool invocation issued by an agent.

    Agents never call clients directly; they build a ``ToolCall`` and hand it to
    the registry, which validates ``arguments`` against the tool's declared
    argument model before executing it.
    """

    call_id: str = Field(default_factory=_new_id, description="Unique id for this call.")
    tool: str = Field(description="Registered tool name, e.g. 'prometheus.query_range'.")
    agent: str = Field(description="Name of the agent issuing the call.")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments validated against the tool's argument model.",
    )
    requested_at: datetime = Field(default_factory=_now, description="Call issue time.")


class ToolResult(BaseModel):
    """The outcome of a :class:`ToolCall`, in a shape every agent can consume."""

    call_id: str = Field(description="Matches the originating ToolCall.call_id.")
    tool: str = Field(description="Registered tool name that produced this result.")
    agent: str = Field(description="Agent that issued the originating call.")
    ok: bool = Field(description="Whether the tool completed without raising.")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Validated result payload, keyed per the tool's result model.",
    )
    error: str | None = Field(default=None, description="Error text when ok is False.")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Wall-clock duration.")
    completed_at: datetime = Field(default_factory=_now, description="Completion time.")


# --------------------------------------------------------------------------
# Monitoring Agent output
# --------------------------------------------------------------------------


class Severity(str, Enum):
    """Incident severity derived from spike magnitude."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentSignal(BaseModel):
    """A detected metric anomaly: what broke, when, and how badly."""

    service: str = Field(description="Service the metric was filtered to.")
    metric_name: str = Field(description="Human-readable metric label.")
    query: str = Field(description="Exact PromQL executed to produce the series.")
    start_timestamp: datetime = Field(
        description="Timestamp of the first sample that breached the baseline."
    )
    detected_at: datetime = Field(
        default_factory=_now,
        description="When the Monitoring Agent observed the anomaly.",
    )
    baseline_value: float = Field(
        ge=0.0, description="Mean of the leading baseline window."
    )
    peak_value: float = Field(ge=0.0, description="Maximum value inside the spike.")
    spike_magnitude: float = Field(
        ge=0.0,
        description="Peak divided by baseline; 1.0 means no change.",
    )
    z_score: float = Field(description="Peak z-score against the baseline window.")
    severity: Severity = Field(description="Bucketed severity.")
    sample_count: int = Field(ge=0, description="Number of samples in the series.")
    evidence: list[str] = Field(
        default_factory=list,
        description="Plain-language statements supporting the detection.",
    )


# --------------------------------------------------------------------------
# Code Analysis Agent output
# --------------------------------------------------------------------------


class CommitCandidate(BaseModel):
    """A commit in the incident window, scored for likely blame."""

    sha: str = Field(description="Commit SHA.")
    author: str = Field(description="Author login or git name.")
    message: str = Field(description="Commit message headline.")
    files_changed: list[str] = Field(
        default_factory=list, description="Paths touched by the commit."
    )
    timestamp: datetime = Field(description="Commit authored time, UTC.")
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Heuristic blame score; see agents/code_analysis.py for the formula.",
    )
    relevance_reasons: list[str] = Field(
        default_factory=list,
        description="Why this commit scored the way it did.",
    )
    minutes_before_incident: float = Field(
        description="Positive when the commit landed before the incident started."
    )
    url: str = Field(default="", description="Commit web URL when known.")


# --------------------------------------------------------------------------
# Ops Agent output
# --------------------------------------------------------------------------


class RiskLevel(str, Enum):
    """How dangerous applying the fix would be."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FixActionKind(str, Enum):
    """The mechanism a fix action uses."""

    ENV_OVERRIDE = "env_override"
    FILE_REPLACE = "file_replace"
    REVERT_COMMIT = "revert_commit"


class FixAction(BaseModel):
    """One concrete, machine-applicable step of a proposed fix.

    Kept deliberately narrow so the dry-run sandbox can apply it without an
    interpreter for arbitrary shell.
    """

    kind: FixActionKind = Field(description="Action mechanism.")
    target: str = Field(
        description="Env var name for env_override, repo-relative path for file_replace, "
        "or commit SHA for revert_commit."
    )
    value: str = Field(default="", description="New value or full replacement content.")
    reason: str = Field(default="", description="Why this action addresses the root cause.")


class ProposedFix(BaseModel):
    """The Ops Agent's remediation proposal. Never applied without a dry run."""

    summary: str = Field(description="One-line description of the fix.")
    root_cause: str = Field(description="Stated cause, grounded in the two prior agents.")
    actions: list[FixAction] = Field(
        default_factory=list, description="Ordered actions to apply in the sandbox."
    )
    target_files: list[str] = Field(
        default_factory=list, description="Files the fix expects to affect."
    )
    referenced_commits: list[str] = Field(
        default_factory=list, description="SHAs the reasoning cites."
    )
    risk_level: RiskLevel = Field(description="Blast-radius assessment.")
    confidence: float = Field(ge=0.0, le=1.0, description="Ops Agent confidence.")
    source: str = Field(
        description="'anthropic' when produced by an LLM call, 'heuristic' for the "
        "deterministic offline planner."
    )
    rationale: list[str] = Field(
        default_factory=list, description="Reasoning steps behind the proposal."
    )


# --------------------------------------------------------------------------
# Dry-run validation output
# --------------------------------------------------------------------------


class SandboxCheck(BaseModel):
    """A single assertion executed against the sandboxed service."""

    name: str = Field(description="Check identifier.")
    passed: bool = Field(description="Whether the assertion held.")
    detail: str = Field(default="", description="Observed value or failure reason.")


class DryRunResult(BaseModel):
    """Outcome of validating a fix in an isolated environment."""

    passed: bool = Field(description="True only when every check passed.")
    method: str = Field(description="Isolation mechanism used, e.g. 'docker'.")
    attempt: int = Field(ge=1, description="1-based attempt number for this run.")
    exit_code: int | None = Field(
        default=None, description="Exit code of the sandbox build/run step."
    )
    duration_seconds: float = Field(ge=0.0, description="Wall-clock duration.")
    checks: list[SandboxCheck] = Field(
        default_factory=list, description="Individual assertions and their outcomes."
    )
    logs: str = Field(default="", description="Captured build and container logs.")
    rejection_reason: str | None = Field(
        default=None, description="Why the fix was rejected, when it failed."
    )
    image_tag: str | None = Field(default=None, description="Throwaway image tag used.")


# --------------------------------------------------------------------------
# Graph state
# --------------------------------------------------------------------------


class RunStatus(str, Enum):
    """Terminal disposition of a swarm run."""

    RUNNING = "running"
    NO_INCIDENT = "no_incident"
    READY_TO_APPLY = "ready_to_apply"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    ERROR = "error"


class SwarmConfig(BaseModel):
    """Per-run inputs. Carried in state so every node sees the same settings."""

    service: str = Field(default="checkout-api", description="Service under analysis.")
    metric_query: str = Field(
        default="",
        description="PromQL override. Empty means use the default error-ratio query.",
    )
    lookback_minutes: int = Field(
        default=60, ge=5, description="How far back to pull metrics."
    )
    step_seconds: int = Field(default=30, ge=1, description="query_range resolution.")
    z_threshold: float = Field(
        default=3.0, description="Z-score above which a sample counts as anomalous."
    )
    min_spike_ratio: float = Field(
        default=2.0,
        ge=1.0,
        description="Peak/baseline floor, so tiny absolute changes are ignored.",
    )
    baseline_fraction: float = Field(
        default=0.5,
        gt=0.0,
        lt=1.0,
        description="Leading fraction of the series treated as the baseline window.",
    )
    commit_window_before_minutes: int = Field(
        default=60, ge=0, description="Minutes before the incident to search commits."
    )
    commit_window_after_minutes: int = Field(
        default=5, ge=0, description="Minutes after the incident to search commits."
    )
    repository: str = Field(
        default="", description="owner/name for the live GitHub commit source."
    )
    service_paths: list[str] = Field(
        default_factory=list,
        description="Paths considered to belong to the service, for blame scoring.",
    )
    commit_source: str = Field(
        default="fixture", description="'live' for the GitHub API, 'fixture' for replay."
    )
    runtime_env: dict[str, str] = Field(
        default_factory=dict,
        description="Configuration the service is currently running with. The Ops Agent "
        "diffs this against the service contract, and the sandbox boots with it.",
    )
    max_repair_attempts: int = Field(
        default=1,
        ge=0,
        description="Ops Agent retries allowed after a failed dry run before human review.",
    )
    scenario_id: str | None = Field(
        default=None, description="Benchmark scenario id when running seeded data."
    )


class SwarmState(BaseModel):
    """The single shared object that flows through every LangGraph node.

    Downstream agents read upstream fields directly off this state; the Code
    Analysis Agent, for example, windows its commit search on
    ``incident.start_timestamp``.
    """

    run_id: str = Field(default_factory=_new_id, description="Unique run identifier.")
    config: SwarmConfig = Field(default_factory=SwarmConfig, description="Run inputs.")

    incident: IncidentSignal | None = Field(
        default=None, description="Monitoring Agent output."
    )
    commit_candidates: list[CommitCandidate] = Field(
        default_factory=list, description="Code Analysis Agent output, best first."
    )
    proposed_fix: ProposedFix | None = Field(
        default=None, description="Ops Agent output, pending validation."
    )
    dry_run_results: list[DryRunResult] = Field(
        default_factory=list, description="One entry per validation attempt."
    )

    tool_calls: list[ToolCall] = Field(
        default_factory=list, description="Every tool call issued during the run."
    )
    tool_results: list[ToolResult] = Field(
        default_factory=list, description="Matching results for tool_calls."
    )

    repair_attempts: int = Field(
        default=0, ge=0, description="Times the Ops Agent has re-planned after a failure."
    )
    status: RunStatus = Field(
        default=RunStatus.RUNNING, description="Current disposition of the run."
    )
    current_node: str = Field(default="start", description="Node currently executing.")
    completed_nodes: list[str] = Field(
        default_factory=list, description="Nodes finished, in order."
    )
    node_durations: dict[str, float] = Field(
        default_factory=dict, description="Seconds spent per node."
    )
    errors: list[str] = Field(default_factory=list, description="Accumulated errors.")
    human_review_reason: str | None = Field(
        default=None, description="Why the run was routed to a human."
    )
    started_at: datetime = Field(default_factory=_now, description="Run start time.")
    finished_at: datetime | None = Field(default=None, description="Run end time.")

    @property
    def latest_dry_run(self) -> DryRunResult | None:
        """Most recent validation attempt, if any."""

        return self.dry_run_results[-1] if self.dry_run_results else None

    @property
    def elapsed_seconds(self) -> float:
        """Wall-clock duration of the run so far."""

        end = self.finished_at or _now()
        return (end - self.started_at).total_seconds()
