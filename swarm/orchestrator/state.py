from __future__ import annotations

from pydantic import BaseModel, Field


class AgentResult(BaseModel):
    """Per-agent outcome stored on the shared workflow state."""

    agent: str = Field(description="Agent name that produced the result.")
    success: bool = Field(description="Whether the agent completed successfully.")
    data: dict[str, object] = Field(
        default_factory=dict,
        description="Structured result payload returned by the agent.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence citations supporting downstream decisions.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score assigned to this result.",
    )


class SwarmState(BaseModel):
    """Shared state object that flows through orchestrator workflows."""

    task_id: str = Field(description="Workflow identifier for the current run.")
    workflow_type: str = Field(description='Workflow category: "deploy", "incident", or "monitor".')
    project_path: str | None = Field(
        default=None,
        description="Filesystem path to the analyzed project.",
    )
    project_metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Aggregated scan metadata about the project.",
    )
    agent_results: dict[str, AgentResult] = Field(
        default_factory=dict,
        description="Results keyed by agent name.",
    )
    dockerfile: str | None = Field(
        default=None,
        description="Generated Dockerfile content when available.",
    )
    docker_compose: str | None = Field(
        default=None,
        description="Generated docker-compose content when available.",
    )
    k8s_manifests: dict[str, str] = Field(
        default_factory=dict,
        description="Generated Kubernetes manifests keyed by filename.",
    )
    cicd_pipeline: str | None = Field(
        default=None,
        description="Generated CI/CD pipeline content when available.",
    )
    alert_data: dict[str, object] = Field(
        default_factory=dict,
        description="Alert metadata used during incident workflows.",
    )
    root_cause_hypothesis: str | None = Field(
        default=None,
        description="Best-supported hypothesis for an incident.",
    )
    fix_diff: str | None = Field(
        default=None,
        description="Unified diff for a proposed incident fix.",
    )
    current_step: str = Field(
        default="init",
        description="Current workflow step label.",
    )
    completed_steps: list[str] = Field(
        default_factory=list,
        description="Ordered list of completed workflow steps.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Human-readable errors accumulated during execution.",
    )
    requires_human_approval: bool = Field(
        default=False,
        description="Whether workflow progression is blocked on human approval.",
    )
    approval_status: str | None = Field(
        default=None,
        description='Approval status: null, "pending", "approved", or "rejected".',
    )
    sandbox_cluster_id: str | None = Field(
        default=None,
        description="Identifier for the ephemeral sandbox cluster used in validation.",
    )
    sandbox_test_passed: bool = Field(
        default=False,
        description="Whether the sandbox verification stage succeeded.",
    )
    deployment_summary: str | None = Field(
        default=None,
        description="Captain-authored summary of the current deployment plan status.",
    )
    iteration_count: int = Field(
        default=0,
        ge=0,
        description="Total number of orchestrator retries triggered so far.",
    )
    max_iterations: int = Field(
        default=5,
        ge=1,
        description="Maximum number of times the same workflow step may repeat.",
    )
    step_iterations: dict[str, int] = Field(
        default_factory=dict,
        description="Execution counts keyed by workflow step name.",
    )
    decision_log: list[dict[str, object]] = Field(
        default_factory=list,
        description="History of Captain decisions made during this workflow.",
    )
