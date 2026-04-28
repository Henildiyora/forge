"""Tests for the FORGE audit log."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge.cli.main import app
from forge.core import audit
from forge.core.audit import AuditLog


def test_audit_log_appends_entries(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.log")
    entry = log.append(
        actor="k8s_specialist",
        action="kubectl_apply",
        target="namespace=demo",
        task_id="task-1",
        evidence=["sandbox passed"],
    )
    assert entry.action == "kubectl_apply"
    assert entry.task_id == "task-1"

    entries = log.read_all()
    assert len(entries) == 1
    assert entries[0].target == "namespace=demo"


def test_audit_log_is_append_only(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.log")
    log.append(actor="a", action="other", target="t1")
    log.append(actor="b", action="other", target="t2")

    raw = (tmp_path / "audit.log").read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["actor"] == "a"
    assert parsed[1]["actor"] == "b"


def test_record_helper_is_no_op_without_default(tmp_path: Path) -> None:
    audit.configure_default_audit_log(tmp_path / "stub.log")
    entry = audit.record(actor="x", action="other", target="t")
    assert entry is not None
    assert (tmp_path / "stub.log").exists()


def test_audit_cli_renders_table(tmp_path: Path) -> None:
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    log = AuditLog(forge_dir / "audit.log")
    log.append(
        actor="forge_cli",
        action="artifact_written",
        target=str(forge_dir / "generated"),
        task_id="task-77",
        evidence=["dockerfile generated"],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["audit", str(tmp_path), "--tail", "5", "--raw"])

    assert result.exit_code == 0
    normalized = "".join(result.stdout.split())
    assert "artifact_written" in normalized
    assert "task-77" in normalized


def test_audit_cli_handles_missing_log(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["audit", str(tmp_path)])
    assert result.exit_code == 0
    normalized = " ".join(result.stdout.split())
    assert "has not recorded" in normalized
