from __future__ import annotations

from typing import Annotated, Literal

import typer

from swarm.core.approvals import approval_store


def list_approvals(
    status: Annotated[
        str | None,
        typer.Option("--status", help="Optional status filter: pending, granted, or rejected."),
    ] = None,
) -> None:
    """List approval requests created by the incident workflow."""

    if status is not None and status not in {"pending", "granted", "rejected"}:
        raise typer.BadParameter("status must be one of: pending, granted, rejected")
    if status is not None:
        if status == "pending":
            narrowed_status: Literal["pending", "granted", "rejected"] = "pending"
        elif status == "granted":
            narrowed_status = "granted"
        else:
            narrowed_status = "rejected"
        requests = approval_store.list_requests(status=narrowed_status)
    else:
        requests = approval_store.list_requests()
    if not requests:
        typer.echo("No approval requests found.")
        return
    for request in requests:
        typer.echo(
            f"{request.id} | {request.status} | {request.severity} | {request.summary}"
        )


def grant_approval(
    approval_id: Annotated[str, typer.Argument()],
    reviewer: Annotated[str, typer.Option("--reviewer")] = "human",
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Grant a pending approval request."""

    request = approval_store.resolve_request(
        approval_id,
        status="granted",
        reviewer=reviewer,
        resolution_note=note,
    )
    if request is None:
        raise typer.BadParameter(f"approval request not found: {approval_id}")
    typer.echo(f"Granted approval: {request.id}")


def reject_approval(
    approval_id: Annotated[str, typer.Argument()],
    reviewer: Annotated[str, typer.Option("--reviewer")] = "human",
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Reject a pending approval request."""

    request = approval_store.resolve_request(
        approval_id,
        status="rejected",
        reviewer=reviewer,
        resolution_note=note,
    )
    if request is None:
        raise typer.BadParameter(f"approval request not found: {approval_id}")
    typer.echo(f"Rejected approval: {request.id}")
