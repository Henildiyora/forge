from __future__ import annotations

from pydantic import BaseModel, Field

from forge.agents.remediation.agent import resume_incident_remediation
from forge.core.builds import BuildExecutionResult, resume_live_build
from forge.core.checkpoints import CheckpointStore
from forge.core.config import Settings


class ResumeOutcome(BaseModel):
    """Normalized outcome when FORGE resumes a paused workflow."""

    workflow_type: str = Field(description="Workflow category that was resumed.")
    summary: str = Field(description="Human-readable resume outcome.")
    build_result: BuildExecutionResult | None = Field(default=None)
    incident_result: dict[str, object] | None = Field(default=None)


async def resume_approved_workflow(
    *,
    settings: Settings,
    checkpoint_store: CheckpointStore,
    task_id: str,
    approved_by: str,
) -> ResumeOutcome | None:
    """Resume the checkpointed workflow waiting on approval."""

    checkpoint = await checkpoint_store.load(task_id)
    if checkpoint is None:
        return None
    if checkpoint.workflow_type == "build":
        build_result = await resume_live_build(
            settings=settings,
            checkpoint_store=checkpoint_store,
            task_id=task_id,
            approved_by=approved_by,
        )
        if build_result is None:
            return None
        return ResumeOutcome(
            workflow_type="build",
            summary="Resumed live build deployment.",
            build_result=build_result,
        )
    if checkpoint.workflow_type == "incident":
        proposal = await resume_incident_remediation(
            settings=settings,
            checkpoint_store=checkpoint_store,
            task_id=task_id,
            approved_by=approved_by,
        )
        if proposal is None:
            return None
        await checkpoint_store.delete(task_id)
        return ResumeOutcome(
            workflow_type="incident",
            summary="Loaded approved incident remediation plan.",
            incident_result=proposal.model_dump(mode="json"),
        )
    return None
