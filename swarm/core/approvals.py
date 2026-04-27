from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

ApprovalStatus = Literal["pending", "granted", "rejected"]


class ApprovalRequest(BaseModel):
    """Structured approval record created by incident workflows."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str = Field(description="Workflow task that created the request.")
    workflow_type: str = Field(description="Workflow category such as deploy or incident.")
    severity: Literal["low", "medium", "high", "critical"] = Field(
        description="Captain-assigned incident severity.",
    )
    summary: str = Field(description="Short incident summary shown to approvers.")
    reason: str = Field(description="Why approval is being requested.")
    proposed_action: str = Field(description="The action awaiting approval.")
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting the approval request.",
    )
    status: ApprovalStatus = Field(default="pending")
    reviewer: str | None = Field(default=None, description="Human who resolved the request.")
    resolution_note: str | None = Field(
        default=None,
        description="Optional note recorded with the approval decision.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalStore:
    """In-memory approval registry used by the current sprint surfaces."""

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self._order: list[str] = []
        self._lock = Lock()

    def create_request(
        self,
        *,
        task_id: str,
        workflow_type: str,
        severity: Literal["low", "medium", "high", "critical"],
        summary: str,
        reason: str,
        proposed_action: str,
        evidence: list[str],
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            task_id=task_id,
            workflow_type=workflow_type,
            severity=severity,
            summary=summary,
            reason=reason,
            proposed_action=proposed_action,
            evidence=evidence,
        )
        with self._lock:
            self._requests[request.id] = request
            self._order.append(request.id)
        return request.model_copy(deep=True)

    def list_requests(self, *, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        with self._lock:
            ordered = [self._requests[request_id] for request_id in self._order]
        if status is not None:
            ordered = [request for request in ordered if request.status == status]
        return [request.model_copy(deep=True) for request in reversed(ordered)]

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        with self._lock:
            request = self._requests.get(request_id)
        return request.model_copy(deep=True) if request is not None else None

    def resolve_request(
        self,
        request_id: str,
        *,
        status: Literal["granted", "rejected"],
        reviewer: str,
        resolution_note: str | None = None,
    ) -> ApprovalRequest | None:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                return None
            updated = request.model_copy(
                update={
                    "status": status,
                    "reviewer": reviewer,
                    "resolution_note": resolution_note,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._requests[request_id] = updated
        return updated.model_copy(deep=True)

    def snapshot(self) -> list[ApprovalRequest]:
        with self._lock:
            ordered = [self._requests[request_id] for request_id in self._order]
        return [request.model_copy(deep=True) for request in ordered]

    def restore(self, requests: list[ApprovalRequest]) -> None:
        with self._lock:
            self._requests = {request.id: request.model_copy(deep=True) for request in requests}
            self._order = [request.id for request in requests]

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._order.clear()


approval_store = ApprovalStore()
