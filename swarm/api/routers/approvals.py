from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from swarm.core.approvals import ApprovalRequest, approval_store

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
