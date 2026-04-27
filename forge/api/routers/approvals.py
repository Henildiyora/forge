from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from forge.api.dependencies import get_bus, get_checkpoint_store, get_settings
from forge.core.approvals import ApprovalRequest, approval_store
from forge.core.checkpoints import CheckpointStore
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus
from forge.core.resume import resume_approved_workflow

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalResolutionInput(BaseModel):
    reviewer: str = Field(default="human")
    note: str | None = Field(default=None)


@router.get("", response_model=list[ApprovalRequest])
async def list_approvals(status: str | None = None) -> list[ApprovalRequest]:
    if status is None:
        return approval_store.list_requests()
    if status == "pending":
        narrowed_status: Literal["pending", "granted", "rejected"] = "pending"
    elif status == "granted":
        narrowed_status = "granted"
    elif status == "rejected":
        narrowed_status = "rejected"
    else:
        raise HTTPException(status_code=400, detail="invalid approval status")
    return approval_store.list_requests(status=narrowed_status)


@router.get("/pending", response_model=list[ApprovalRequest])
async def list_pending_approvals() -> list[ApprovalRequest]:
    return approval_store.list_requests(status="pending")


@router.get("/{task_id}", response_class=HTMLResponse)
async def get_approval_page(task_id: str) -> str:
    request = approval_store.get_by_task_id(task_id)
    if request is None:
        raise HTTPException(status_code=404, detail="approval request not found")
    return (
        "<html><body>"
        f"<h1>FORGE Approval for {request.summary}</h1>"
        f"<p>{request.reason}</p>"
        f"<form method='post' action='/api/v1/approvals/{task_id}/approve'>"
        "<button type='submit'>Approve</button></form>"
        f"<form method='post' action='/api/v1/approvals/{task_id}/reject'>"
        "<button type='submit'>Reject</button></form>"
        "</body></html>"
    )


@router.post("/{task_id}/approve")
async def approve_task_id(
    task_id: str,
    bus: Annotated[MessageBus, Depends(get_bus)],
    checkpoint_store: Annotated[CheckpointStore, Depends(get_checkpoint_store)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    request = approval_store.get_by_task_id(task_id)
    if request is None:
        raise HTTPException(status_code=404, detail="approval request not found")
    approval_store.resolve_request(request.id, status="granted", reviewer="web_ui")
    await bus.publish(
        SwarmEvent(
            type=EventType.APPROVAL_GRANTED,
            task_id=task_id,
            source_agent="web_ui",
        )
    )
    await resume_approved_workflow(
        settings=settings.model_copy(update={"dry_run_mode": False}),
        checkpoint_store=checkpoint_store,
        task_id=task_id,
        approved_by="web_ui",
    )
    return {"status": "approved"}


@router.post("/{task_id}/reject")
async def reject_task_id(
    task_id: str,
    bus: Annotated[MessageBus, Depends(get_bus)],
) -> dict[str, str]:
    request = approval_store.get_by_task_id(task_id)
    if request is None:
        raise HTTPException(status_code=404, detail="approval request not found")
    approval_store.resolve_request(request.id, status="rejected", reviewer="web_ui")
    await bus.publish(
        SwarmEvent(
            type=EventType.APPROVAL_REJECTED,
            task_id=task_id,
            source_agent="web_ui",
        )
    )
    return {"status": "rejected"}


@router.post("/{approval_id}/grant", response_model=ApprovalRequest)
async def grant_approval(
    approval_id: str,
    payload: ApprovalResolutionInput,
) -> ApprovalRequest:
    request = approval_store.resolve_request(
        approval_id,
        status="granted",
        reviewer=payload.reviewer,
        resolution_note=payload.note,
    )
    if request is None:
        raise HTTPException(status_code=404, detail="approval request not found")
    return request


@router.post("/{approval_id}/reject", response_model=ApprovalRequest)
async def reject_approval(
    approval_id: str,
    payload: ApprovalResolutionInput,
) -> ApprovalRequest:
    request = approval_store.resolve_request(
        approval_id,
        status="rejected",
        reviewer=payload.reviewer,
        resolution_note=payload.note,
    )
    if request is None:
        raise HTTPException(status_code=404, detail="approval request not found")
    return request
