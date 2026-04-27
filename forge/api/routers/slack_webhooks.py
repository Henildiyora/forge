from __future__ import annotations

from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request

from forge.api.dependencies import get_bus, get_checkpoint_store, get_settings
from forge.core.checkpoints import CheckpointStore
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus
from forge.core.resume import resume_approved_workflow
from forge.integrations.slack import parse_slack_payload, verify_slack_signature

router = APIRouter(prefix="/webhooks/slack", tags=["slack"])


@router.post("/actions")
async def handle_slack_action(
    request: Request,
    bus: Annotated[MessageBus, Depends(get_bus)],
    settings: Annotated[Settings, Depends(get_settings)],
    checkpoint_store: Annotated[CheckpointStore, Depends(get_checkpoint_store)],
) -> dict[str, object]:
    """Handle Slack interactive button clicks and resume waiting workflows."""

    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    signing_secret = (
        settings.slack_signing_secret.get_secret_value()
        if settings.slack_signing_secret is not None
        else ""
    )
    if signing_secret and not verify_slack_signature(
        signing_secret=signing_secret,
        timestamp=timestamp,
        body=body,
        signature=signature,
    ):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    parsed_form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    payload_values = parsed_form.get("payload", [])
    raw_payload = payload_values[0] if payload_values else None
    if not isinstance(raw_payload, str):
        raise HTTPException(status_code=400, detail="Missing Slack payload")
    payload = parse_slack_payload(raw_payload)
    actions = payload.get("actions", [])
    if not isinstance(actions, list) or not actions:
        raise HTTPException(status_code=400, detail="Missing Slack action")
    action = actions[0]
    if not isinstance(action, dict):
        raise HTTPException(status_code=400, detail="Invalid Slack action")

    action_id = str(action.get("action_id", ""))
    task_id = str(action.get("value", ""))
    if action_id.startswith("approve_"):
        event_type = EventType.APPROVAL_GRANTED
        await resume_approved_workflow(
            settings=settings.model_copy(update={"dry_run_mode": False}),
            checkpoint_store=checkpoint_store,
            task_id=task_id,
            approved_by="slack",
        )
    elif action_id.startswith("reject_"):
        event_type = EventType.APPROVAL_REJECTED
    else:
        event_type = EventType.REINVESTIGATION_REQUESTED
    await bus.publish(
        SwarmEvent(
            type=event_type,
            task_id=task_id,
            source_agent="slack_webhook",
            payload={"slack_payload": payload},
        )
    )
    return {"ok": True}
