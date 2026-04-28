"""End-to-end-ish integration test of the Slack webhook approval flow.

Sends a real HMAC-signed Slack interactive payload at the FastAPI router and
asserts that:

1. Signature verification gates the route (bad signature -> 403).
2. A valid signed approve action publishes APPROVAL_GRANTED on the bus.
3. The audit log gains an ``approval_granted`` entry tagged with the task id.
4. The approval store records the approval.

Uses the in-memory message bus and approval store, so no Redis or Slack
account is required.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.api.dependencies import get_bus, get_settings
from forge.core import audit
from forge.core.approvals import approval_store
from forge.core.audit import AuditLog
from forge.core.config import Settings
from forge.core.message_bus import MessageBus

SIGNING_SECRET = "test-signing-secret"


def _signed_request(
    body: bytes,
    *,
    timestamp: str | None = None,
    secret: str = SIGNING_SECRET,
) -> dict[str, str]:
    actual_timestamp = timestamp or str(int(time.time()))
    basestring = f"v0:{actual_timestamp}:{body.decode('utf-8')}".encode()
    digest = "v0=" + hmac.new(
        secret.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()
    return {
        "X-Slack-Request-Timestamp": actual_timestamp,
        "X-Slack-Signature": digest,
    }


def _slack_action_body(*, action_id: str, task_id: str) -> bytes:
    payload = {
        "type": "block_actions",
        "user": {"id": "U123", "name": "alice"},
        "channel": {"id": "C123", "name": "approvals"},
        "actions": [
            {
                "action_id": action_id,
                "value": task_id,
                "type": "button",
            }
        ],
    }
    encoded = urlencode({"payload": json.dumps(payload)})
    return encoded.encode("utf-8")


@pytest.fixture
def configured_app(tmp_path: Path) -> TestClient:
    audit.configure_default_audit_log(tmp_path / "audit.log")
    approval_store.reset()

    settings = Settings(
        app_env="test",
        slack_signing_secret=SIGNING_SECRET,  # type: ignore[arg-type]
    )
    captured_bus = MessageBus.in_memory(settings)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_bus] = lambda: captured_bus
    client = TestClient(app)
    client.bus = captured_bus  # type: ignore[attr-defined]
    return client


SLACK_ROUTE = "/api/v1/webhooks/slack/actions"


def test_slack_webhook_rejects_bad_signature(
    configured_app: TestClient,
    tmp_path: Path,
) -> None:
    body = _slack_action_body(action_id="approve_task-77", task_id="task-77")
    headers = _signed_request(body, secret="not-the-real-secret")
    response = configured_app.post(
        SLACK_ROUTE,
        content=body,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 403
    assert "Invalid Slack" in response.text


def test_slack_webhook_resumes_on_valid_approve(
    configured_app: TestClient,
    tmp_path: Path,
) -> None:
    body = _slack_action_body(action_id="approve_task-99", task_id="task-99")
    headers = _signed_request(body)
    response = configured_app.post(
        SLACK_ROUTE,
        content=body,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}

    captured_bus: MessageBus = configured_app.bus  # type: ignore[attr-defined]
    broadcast_stream = captured_bus.settings.broadcast_stream
    client_streams = captured_bus._client._streams  # type: ignore[attr-defined]
    assert client_streams[broadcast_stream], "Webhook should publish a SwarmEvent"
    published_fields = client_streams[broadcast_stream][-1][1]
    assert published_fields["type"] == "approval.granted"
    assert published_fields["task_id"] == "task-99"

    log = AuditLog(tmp_path / "audit.log")
    grants = [e for e in log.read_all() if e.action == "approval_granted"]
    assert grants, "audit log must record the approval"
    assert grants[-1].task_id == "task-99"
    assert any("alice" in evidence for evidence in grants[-1].evidence)


def test_slack_webhook_rejects_path_publishes_rejection(
    configured_app: TestClient,
    tmp_path: Path,
) -> None:
    body = _slack_action_body(action_id="reject_task-55", task_id="task-55")
    headers = _signed_request(body)
    response = configured_app.post(
        SLACK_ROUTE,
        content=body,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200

    captured_bus: MessageBus = configured_app.bus  # type: ignore[attr-defined]
    broadcast_stream = captured_bus.settings.broadcast_stream
    client_streams = captured_bus._client._streams  # type: ignore[attr-defined]
    published_fields = client_streams[broadcast_stream][-1][1]
    assert published_fields["type"] == "approval.rejected"

    log = AuditLog(tmp_path / "audit.log")
    rejects = [e for e in log.read_all() if e.action == "approval_rejected"]
    assert rejects
    assert rejects[-1].task_id == "task-55"
