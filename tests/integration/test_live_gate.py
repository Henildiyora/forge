"""Integration coverage for ``LiveExecutionGate``.

Every failure mode in :meth:`KubectlClient.live_execution_gate` must block
``apply_manifests_live`` and emit an audit ``live_gate_blocked`` entry. This
test enumerates each failure path and asserts both behaviours.

Runs without a real cluster: a stub kubectl runner records command
invocations so we can also assert that no kubectl write reaches the runner
when the gate refuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from forge.agents.k8s_specialist.kubectl_client import (
    CommandResult,
    KubectlClient,
    LiveExecutionContext,
    SupportsKubectlRunner,
)
from forge.core import audit
from forge.core.audit import AuditLog
from forge.core.config import Settings
from forge.core.exceptions import ConfigurationError


@dataclass
class _RecordingRunner(SupportsKubectlRunner):
    invocations: list[tuple[list[str], str | None]] = field(default_factory=list)

    async def run(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> CommandResult:
        self.invocations.append((list(args), input_text))
        return CommandResult(stdout="ok", stderr="", returncode=0)


@pytest.fixture
def audit_log_path(tmp_path: Path) -> Path:
    log_path = tmp_path / "audit.log"
    audit.configure_default_audit_log(log_path)
    yield log_path
    audit.configure_default_audit_log(tmp_path / "_unused.log")


def _live_settings(**overrides: object) -> Settings:
    base = {
        "app_env": "test",
        "dry_run_mode": False,
        "require_human_approval": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _good_context() -> LiveExecutionContext:
    return LiveExecutionContext(
        sandbox_test_passed=True,
        approval_status="approved",
        task_id="task-good",
        dry_run_passed=True,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gate_blocks_dry_run_mode(audit_log_path: Path) -> None:
    settings = _live_settings(dry_run_mode=True)
    runner = _RecordingRunner()
    client = KubectlClient(settings=settings, runner=runner)

    with pytest.raises(ConfigurationError, match="dry_run_mode"):
        await client.apply_manifests_live(
            {"deployment.yaml": "kind: Deployment"},
            context=_good_context(),
            namespace="demo",
            approved_by="alice",
        )
    assert runner.invocations == []
    entries = AuditLog(audit_log_path).read_all()
    assert any(e.action == "live_gate_blocked" for e in entries)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gate_blocks_when_sandbox_did_not_pass(audit_log_path: Path) -> None:
    settings = _live_settings()
    runner = _RecordingRunner()
    client = KubectlClient(settings=settings, runner=runner)
    context = LiveExecutionContext(
        sandbox_test_passed=False,
        approval_status="approved",
        task_id="task-1",
        dry_run_passed=True,
    )
    with pytest.raises(ConfigurationError, match="Sandbox"):
        await client.apply_manifests_live(
            {"deployment.yaml": "kind: Deployment"},
            context=context,
            namespace="demo",
        )
    assert runner.invocations == []
    assert any(
        e.action == "live_gate_blocked"
        for e in AuditLog(audit_log_path).read_all()
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gate_blocks_when_approval_missing(audit_log_path: Path) -> None:
    settings = _live_settings()
    runner = _RecordingRunner()
    client = KubectlClient(settings=settings, runner=runner)
    context = LiveExecutionContext(
        sandbox_test_passed=True,
        approval_status=None,
        task_id="task-1",
        dry_run_passed=True,
    )
    with pytest.raises(ConfigurationError, match="approval"):
        await client.apply_manifests_live(
            {"deployment.yaml": "kind: Deployment"},
            context=context,
            namespace="demo",
        )
    assert runner.invocations == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gate_blocks_when_task_id_missing(audit_log_path: Path) -> None:
    settings = _live_settings()
    runner = _RecordingRunner()
    client = KubectlClient(settings=settings, runner=runner)
    context = LiveExecutionContext(
        sandbox_test_passed=True,
        approval_status="approved",
        task_id=None,
        dry_run_passed=True,
    )
    with pytest.raises(ConfigurationError, match="task_id"):
        await client.apply_manifests_live(
            {"deployment.yaml": "kind: Deployment"},
            context=context,
            namespace="demo",
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gate_blocks_when_dry_run_did_not_pass(audit_log_path: Path) -> None:
    settings = _live_settings()
    runner = _RecordingRunner()
    client = KubectlClient(settings=settings, runner=runner)
    context = LiveExecutionContext(
        sandbox_test_passed=True,
        approval_status="approved",
        task_id="task-1",
        dry_run_passed=False,
    )
    with pytest.raises(ConfigurationError, match="dry run"):
        await client.apply_manifests_live(
            {"deployment.yaml": "kind: Deployment"},
            context=context,
            namespace="demo",
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gate_allows_when_every_check_passes(audit_log_path: Path) -> None:
    settings = _live_settings()
    runner = _RecordingRunner()
    client = KubectlClient(settings=settings, runner=runner)

    record = await client.apply_manifests_live(
        {"deployment.yaml": "kind: Deployment"},
        context=_good_context(),
        namespace="demo",
        approved_by="alice",
    )

    assert record.applied is True
    assert runner.invocations, "kubectl runner should be invoked when the gate allows"
    apply_entry = next(
        e for e in AuditLog(audit_log_path).read_all() if e.action == "kubectl_apply"
    )
    assert apply_entry.target == "namespace=demo manifests=['deployment.yaml']"
