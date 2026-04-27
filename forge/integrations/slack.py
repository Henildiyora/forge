from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import cast

import httpx

from forge.core.approvals import ApprovalRequest


def build_approval_message(
    request: ApprovalRequest,
    *,
    approval_url: str | None = None,
) -> dict[str, object]:
    """Build the Slack Block Kit payload for an approval request."""

    summary = request.summary if approval_url is None else f"{request.summary}\n{approval_url}"
    return {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"FORGE — {request.summary}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Type:* {request.workflow_type}\n"
                        f"*Severity:* {request.severity}\n\n"
                        f"*Summary:* {summary}\n\n"
                        f"*Reason:* {request.reason}"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Evidence:*\n" + "\n".join(f"- {item}" for item in request.evidence),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": f"approve_{request.task_id}",
                        "value": request.task_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": f"reject_{request.task_id}",
                        "value": request.task_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Investigate More"},
                        "action_id": f"reinvestigate_{request.task_id}",
                        "value": request.task_id,
                    },
                ],
            },
        ]
    }


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify Slack request authenticity using HMAC-SHA256."""

    basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
    digest = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        basestring,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, signature)


async def post_slack_message(
    *,
    webhook_url: str,
    payload: dict[str, object],
) -> None:
    """Send a message to Slack via an incoming webhook."""

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(webhook_url, json=payload)
        response.raise_for_status()


def parse_slack_payload(payload_value: str) -> dict[str, object]:
    """Parse the interactive payload field Slack posts to the webhook."""

    parsed = json.loads(payload_value)
    if not isinstance(parsed, dict):
        raise ValueError("Slack payload must be a JSON object")
    return cast(dict[str, object], parsed)


def received_timestamp() -> str:
    """Return a Slack-style current timestamp value."""

    return str(int(datetime.now(UTC).timestamp()))
