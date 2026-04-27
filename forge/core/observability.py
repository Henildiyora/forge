from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock

from pydantic import BaseModel, Field

from forge.core.approvals import approval_store
from forge.orchestrator.state import SwarmState


class WorkflowRunRecord(BaseModel):
    """Latest observable state for a workflow task."""

    task_id: str
    workflow_type: str
    current_step: str
    completed_steps: list[str] = Field(default_factory=list)
    error_count: int = Field(ge=0)
    latest_errors: list[str] = Field(default_factory=list)
    requires_human_approval: bool = False
    approval_status: str | None = None
    sandbox_test_passed: bool = False
    root_cause_hypothesis: str | None = None
    deployment_summary: str | None = None
    agent_confidences: dict[str, float] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkflowObservabilitySummary(BaseModel):
    """Aggregated dashboard-like summary for the current process."""

    total_runs: int = Field(ge=0)
    runs_by_workflow: dict[str, int] = Field(default_factory=dict)
    runs_in_error: int = Field(ge=0)
    pending_approvals: int = Field(ge=0)
    sandbox_pass_rate: float = Field(ge=0.0, le=1.0)
    average_agent_confidence: float = Field(ge=0.0, le=1.0)
    recent_errors: list[str] = Field(default_factory=list)
    recent_tasks: list[WorkflowRunRecord] = Field(default_factory=list)


class ObservabilityStore:
    """In-memory snapshot store for workflow state visibility."""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRunRecord] = {}
        self._lock = Lock()

    def record_state(self, state: SwarmState) -> WorkflowRunRecord:
        record = WorkflowRunRecord(
            task_id=state.task_id,
            workflow_type=state.workflow_type,
            current_step=state.current_step,
            completed_steps=list(state.completed_steps),
            error_count=len(state.errors),
            latest_errors=state.errors[-5:],
            requires_human_approval=state.requires_human_approval,
            approval_status=state.approval_status,
            sandbox_test_passed=state.sandbox_test_passed,
            root_cause_hypothesis=state.root_cause_hypothesis,
            deployment_summary=state.deployment_summary,
            agent_confidences={
                agent: result.confidence for agent, result in state.agent_results.items()
            },
        )
        with self._lock:
            self._runs[state.task_id] = record
        return record.model_copy(deep=True)

    def recent_runs(self, limit: int = 10) -> list[WorkflowRunRecord]:
        with self._lock:
            records = sorted(
                self._runs.values(),
                key=lambda record: record.updated_at,
                reverse=True,
            )
        return [record.model_copy(deep=True) for record in records[:limit]]

    def summary(self, limit: int = 10) -> WorkflowObservabilitySummary:
        with self._lock:
            records = list(self._runs.values())
        runs_by_workflow: dict[str, int] = {}
        recent_errors: list[str] = []
        confidences: list[float] = []
        sandbox_results = 0
        sandbox_passes = 0
        runs_in_error = 0

        for record in records:
            runs_by_workflow[record.workflow_type] = (
                runs_by_workflow.get(record.workflow_type, 0) + 1
            )
            if record.current_step == "error" or record.error_count > 0:
                runs_in_error += 1
            recent_errors.extend(record.latest_errors)
            confidences.extend(record.agent_confidences.values())
            if record.workflow_type in {"deploy", "incident"}:
                sandbox_results += 1
                if record.sandbox_test_passed:
                    sandbox_passes += 1

        average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        sandbox_pass_rate = sandbox_passes / sandbox_results if sandbox_results else 0.0
        return WorkflowObservabilitySummary(
            total_runs=len(records),
            runs_by_workflow=runs_by_workflow,
            runs_in_error=runs_in_error,
            pending_approvals=len(approval_store.list_requests(status="pending")),
            sandbox_pass_rate=sandbox_pass_rate,
            average_agent_confidence=average_confidence,
            recent_errors=recent_errors[-10:],
            recent_tasks=self.recent_runs(limit=limit),
        )

    def snapshot(self) -> list[WorkflowRunRecord]:
        with self._lock:
            records = list(self._runs.values())
        return [record.model_copy(deep=True) for record in records]

    def restore(self, records: list[WorkflowRunRecord]) -> None:
        with self._lock:
            self._runs = {
                record.task_id: record.model_copy(deep=True) for record in records
            }

    def reset(self) -> None:
        with self._lock:
            self._runs.clear()


observability_store = ObservabilityStore()
