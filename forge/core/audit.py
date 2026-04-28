"""Append-only audit log for actions FORGE takes against any system.

Every action that touches infrastructure (kubectl write, Slack send, cloud
API call, file write outside ``.forge/``, approval grant) MUST be recorded
here. The log lives at ``<project>/.forge/audit.log`` as JSON Lines so it
is easy to grep, tail, and ship to a SIEM.

Trust contract:

* Append-only. Existing lines are never edited or removed by FORGE.
* No secrets are stored. Callers are responsible for redacting tokens.
* Every entry has a UTC ``timestamp``, ``actor`` (which agent/CLI emitted
  it), ``action`` (kubectl_apply, slack_send, ...), ``target`` (a
  scope-specific resource string), ``task_id`` (workflow correlation), and
  optional ``evidence`` and ``approval_id`` references.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

AuditAction = Literal[
    "kubectl_apply",
    "kubectl_rollback",
    "kubectl_delete",
    "slack_send",
    "slack_action_received",
    "approval_granted",
    "approval_rejected",
    "cloud_api_call",
    "fix_applied",
    "rollback_triggered",
    "live_gate_blocked",
    "artifact_written",
    "other",
]


class AuditEntry(BaseModel):
    """One line in the FORGE audit log."""

    timestamp: str = Field(description="UTC ISO 8601 timestamp.")
    actor: str = Field(description="Component that performed the action (agent or CLI).")
    action: AuditAction = Field(description="Verb describing what happened.")
    target: str = Field(description="Resource or recipient affected by the action.")
    task_id: str | None = Field(default=None, description="Workflow correlation id.")
    approval_id: str | None = Field(
        default=None, description="Approval request id when relevant."
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Free-form evidence references supporting the action.",
    )
    detail: dict[str, object] = Field(
        default_factory=dict,
        description="Additional structured context (must not contain secrets).",
    )


class AuditLog:
    """Thread-safe append-only writer for ``.forge/audit.log``."""

    _GLOBAL_LOCK = threading.Lock()

    def __init__(self, log_path: str | os.PathLike[str]) -> None:
        self.log_path = Path(log_path).expanduser()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @classmethod
    def for_workspace(cls, workspace_dir: str | os.PathLike[str]) -> AuditLog:
        return cls(Path(workspace_dir) / "audit.log")

    def append(
        self,
        *,
        actor: str,
        action: AuditAction,
        target: str,
        task_id: str | None = None,
        approval_id: str | None = None,
        evidence: list[str] | None = None,
        detail: dict[str, object] | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            timestamp=datetime.now(UTC).isoformat(),
            actor=actor,
            action=action,
            target=target,
            task_id=task_id,
            approval_id=approval_id,
            evidence=list(evidence or []),
            detail=dict(detail or {}),
        )
        line = entry.model_dump_json() + "\n"
        with self._lock, AuditLog._GLOBAL_LOCK:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
        return entry

    def read_all(self) -> list[AuditEntry]:
        if not self.log_path.exists():
            return []
        entries: list[AuditEntry] = []
        with self.log_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                payload = json.loads(raw)
                entries.append(AuditEntry.model_validate(payload))
        return entries

    def tail(self, count: int) -> list[AuditEntry]:
        all_entries = self.read_all()
        return all_entries[-count:] if count > 0 else all_entries


_DEFAULT_LOG: AuditLog | None = None


def configure_default_audit_log(log_path: str | os.PathLike[str]) -> AuditLog:
    """Set the process-wide default :class:`AuditLog` used by ``record(...)``."""

    global _DEFAULT_LOG
    _DEFAULT_LOG = AuditLog(log_path)
    return _DEFAULT_LOG


def default_audit_log() -> AuditLog | None:
    return _DEFAULT_LOG


def record(
    *,
    actor: str,
    action: AuditAction,
    target: str,
    task_id: str | None = None,
    approval_id: str | None = None,
    evidence: list[str] | None = None,
    detail: dict[str, object] | None = None,
) -> AuditEntry | None:
    """Append a record to the configured default audit log when one exists."""

    log = _DEFAULT_LOG
    if log is None:
        return None
    return log.append(
        actor=actor,
        action=action,
        target=target,
        task_id=task_id,
        approval_id=approval_id,
        evidence=evidence,
        detail=detail,
    )


__all__: Annotated[list[str], "module exports"] = [
    "AuditAction",
    "AuditEntry",
    "AuditLog",
    "configure_default_audit_log",
    "default_audit_log",
    "record",
]
