from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """All event types allowed on the inter-agent message bus."""

    TASK_ASSIGNED = "task.assigned"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"

    CODEBASE_SCAN_REQUESTED = "deploy.codebase_scan_requested"
    CODEBASE_SCAN_COMPLETED = "deploy.codebase_scan_completed"
    CONVERSATION_STARTED = "conversation.started"
    INTENT_INTERPRETED = "conversation.intent_interpreted"
    CLARIFICATION_REQUESTED = "conversation.clarification_requested"
    STRATEGY_RECOMMENDED = "conversation.strategy_recommended"
    STRATEGY_CONFIRMED = "conversation.strategy_confirmed"
    DEPLOYMENT_PLAN_REQUESTED = "deploy.plan_requested"
    DOCKERFILE_GENERATED = "deploy.dockerfile_generated"
    K8S_MANIFESTS_GENERATED = "deploy.k8s_manifests_generated"
    CICD_PIPELINE_GENERATED = "deploy.cicd_pipeline_generated"
    DEPLOYMENT_PLAN_READY = "deploy.plan_ready"
    LIVE_DEPLOYMENT_REQUESTED = "deploy.live_requested"
    LIVE_DEPLOYMENT_STARTED = "deploy.live_started"
    LIVE_DEPLOYMENT_SUCCEEDED = "deploy.live_succeeded"
    LIVE_DEPLOYMENT_FAILED = "deploy.live_failed"
    LIVE_ROLLBACK_TRIGGERED = "deploy.rollback_triggered"
    LIVE_ROLLBACK_COMPLETED = "deploy.rollback_completed"

    SANDBOX_TEST_REQUESTED = "sandbox.test_requested"
    SANDBOX_TEST_PASSED = "sandbox.test_passed"
    SANDBOX_TEST_FAILED = "sandbox.test_failed"

    ALERT_TRIGGERED = "incident.alert_triggered"
    INVESTIGATION_REQUESTED = "incident.investigation_requested"
    ROOT_CAUSE_IDENTIFIED = "incident.root_cause_identified"
    FIX_GENERATED = "incident.fix_generated"
    FIX_VERIFIED = "incident.fix_verified"
    REINVESTIGATION_REQUESTED = "incident.reinvestigation_requested"
    INCIDENT_REMEDIATION_APPROVED = "incident.remediation_approved"
    INCIDENT_REMEDIATION_REJECTED = "incident.remediation_rejected"
    INCIDENT_RESOLVED = "incident.resolved"

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_RESUME_REQUESTED = "approval.resume_requested"

    HEALTH_CHECK_TRIGGERED = "monitor.health_check"
    ANOMALY_DETECTED = "monitor.anomaly_detected"


class SwarmEvent(BaseModel):
    """Normalized event exchanged between agents over Redis Streams."""

    id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this event.",
    )
    type: EventType = Field(
        description="Enumerated event type used for routing and auditing.",
    )
    task_id: str = Field(
        description="Workflow identifier that links related events together.",
    )
    source_agent: str = Field(
        description="Agent name that published the event.",
    )
    target_agent: str | None = Field(
        default=None,
        description="Destination agent. Null indicates broadcast.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured event body for the receiving agent.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Supplemental tracing and routing metadata.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the event was created.",
    )
    parent_event_id: str | None = Field(
        default=None,
        description="Upstream event identifier for event chain tracing.",
    )


class DeadLetterEnvelope(BaseModel):
    """Envelope written to the dead letter queue for failed events."""

    stream: str = Field(description="Stream where the failure happened.")
    error: str = Field(description="Failure reason for the dead-letter entry.")
    original_event: dict[str, object] | None = Field(
        default=None,
        description="Serialized event when decoding succeeded.",
    )
    raw_message: dict[str, str] | None = Field(
        default=None,
        description="Original stream fields when decoding failed.",
    )
    failed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the dead-letter entry was created.",
    )
