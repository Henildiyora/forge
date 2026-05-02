from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge.cli.main import app
from forge.core.approvals import approval_store
from forge.core.exceptions import SandboxToolingError


def test_status_command() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Manager-led build flow" in result.stdout
    assert "approval checkpoints" in result.stdout
    assert "hardening suite" in result.stdout


def test_connect_command_saves_project_preferences(tmp_path: Path) -> None:
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()

    result = runner.invoke(
        app,
        [
            "connect",
            str(project),
            "--backend",
            "ollama",
            "--model",
            "llama3.1:8b",
            "--approval-transport",
            "slack",
            "--cloud-provider",
            "aws",
        ],
    )

    assert result.exit_code == 0
    connection_path = project / ".forge" / "connection.json"
    assert connection_path.exists()
    payload = json.loads(connection_path.read_text(encoding="utf-8"))
    assert payload["llm_backend"] == "ollama"
    assert payload["approval_transport"] == "slack"
    assert payload["cloud_provider"] == "aws"


def test_index_command_persists_scan_result(python_fastapi_project: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["index", str(python_fastapi_project)])

    assert result.exit_code == 0
    index_path = python_fastapi_project / ".forge" / "index.json"
    assert index_path.exists()
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["framework"] == "fastapi"
    assert payload["service_count"] >= 1


def test_ask_command_returns_answer(python_fastapi_project: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ask",
            "Why did FORGE pick a deployment strategy?",
            str(python_fastapi_project),
        ],
    )
    assert result.exit_code == 0
    assert "FORGE" in result.stdout or "deployment" in result.stdout.lower()


def test_build_command_generates_serverless_artifacts(
    tmp_path: Path,
    python_fastapi_project: Path,
) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "artifacts"

    result = runner.invoke(
        app,
        [
            "build",
            str(python_fastapi_project),
            "--goal",
            "Deploy this FastAPI app to aws lambda with a simple serverless setup",
            "--output-dir",
            str(output_dir),
            "--auto-approve",
        ],
        input="1\n",
    )

    assert result.exit_code == 0
    assert "FORGE recommends: serverless" in result.stdout
    assert "Project scan summary" in result.stdout
    assert "Top deployment strategies" in result.stdout
    assert "Quick strategy guide:" in result.stdout
    assert "Open the deployment guide" in result.stdout
    assert (output_dir / "serverless.yml").exists()
    assert (output_dir / "instruction_deploy.md").exists()
    guide = (output_dir / "instruction_deploy.md").read_text(encoding="utf-8")
    assert "<your_project_root>" in guide
    assert "Placeholder legend" in guide
    session_path = python_fastapi_project / ".forge" / "session.json"
    assert session_path.exists()
    session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    assert session_payload["strategy"] == "serverless"


def test_build_command_shows_friendly_message_when_vcluster_is_missing(
    tmp_path: Path,
    python_fastapi_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "artifacts"

    async def _raise_sandbox_error(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise SandboxToolingError(
            "vcluster binary not found at /usr/local/bin/vcluster. "
            "Install vcluster first (macOS: `brew install loft-sh/tap/vcluster`) "
            "or choose the Docker Compose strategy if you only need Docker artifacts."
        )

    monkeypatch.setattr("forge.cli.commands.build.validate_kubernetes_build", _raise_sandbox_error)
    result = runner.invoke(
        app,
        [
            "build",
            str(python_fastapi_project),
            "--goal",
            "Deploy this service to Kubernetes",
            "--output-dir",
            str(output_dir),
            "--auto-approve",
        ],
        input="1\n",
    )

    assert result.exit_code == 1
    assert "Kubernetes sandbox validation cannot run on this machine" in result.stdout
    assert "Install vcluster first" in result.stdout
    assert "Recommended next steps:" in result.stdout
    assert "Install tooling: brew install loft-sh/tap/vcluster" in result.stdout
    assert "choose Docker Compose for a simpler path" in result.stdout
    assert "Traceback" not in result.stdout


def test_build_command_handles_uncertain_goal_without_traceback(
    tmp_path: Path,
    python_fastapi_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "artifacts"

    async def _skip_sandbox(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return None

    monkeypatch.setattr("forge.cli.commands.build.validate_kubernetes_build", _skip_sandbox)
    result = runner.invoke(
        app,
        [
            "build",
            str(python_fastapi_project),
            "--goal",
            "I don't know what to deploy yet, can you suggest the best option?",
            "--output-dir",
            str(output_dir),
            "--auto-approve",
        ],
        input="1\n",
    )

    assert result.exit_code == 0
    assert "FORGE recommends:" in result.stdout
    assert "Quick strategy guide:" in result.stdout
    assert "Traceback" not in result.stdout


def test_monitor_command_can_escalate_to_incident_workflow() -> None:
    approval_store.reset()
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "monitor",
            "payments",
            "--incident",
            "--error-rate",
            "0.11",
            "--latency-p95-ms",
            "900",
            "--restart-count",
            "2",
            "--error-log-count",
            "4",
        ],
    )

    assert result.exit_code == 0
    assert "approval_requested" in result.stdout
    assert "Approval request id:" in result.stdout
    assert len(approval_store.list_requests(status="pending")) == 1


def test_approvals_commands_can_list_and_grant_requests() -> None:
    approval_store.reset()
    request = approval_store.create_request(
        task_id="incident-1",
        workflow_type="incident",
        severity="high",
        summary="Approval needed",
        reason="High severity incident",
        proposed_action="Rollback deployment",
        evidence=["Error rate is elevated."],
    )
    runner = CliRunner()

    list_result = runner.invoke(app, ["approvals", "list"])
    grant_result = runner.invoke(
        app,
        ["approvals", "grant", request.id, "--reviewer", "alice", "--note", "approved"],
    )

    assert list_result.exit_code == 0
    assert request.id in list_result.stdout
    assert grant_result.exit_code == 0
    assert "Granted approval" in grant_result.stdout
    stored = approval_store.get_request(request.id)
    assert stored is not None
    assert stored.status == "granted"


def test_doctor_post_install_checklist() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--quick", "--post-install"])
    assert result.exit_code == 0
    assert "Post-install checklist" in result.stdout
    assert "which forge" in result.stdout


def test_doctor_reports_missing_pipx_path(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    result = runner.invoke(app, ["doctor", "--quick"])

    assert result.exit_code == 0
    assert "pipx PATH" in result.stdout
    assert "pipx ensurepath" in result.stdout
