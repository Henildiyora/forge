from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from swarm.cli.main import app
from swarm.core.approvals import approval_store


def _write_catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "aws": [
                    {
                        "provider": "aws",
                        "service": "eks",
                        "resource_id": "eks-1",
                        "name": "payments-eks",
                        "region": "us-east-1",
                        "account_id": "prod-123",
                        "status": "active",
                        "tags": {"service": "payments"},
                    },
                    {
                        "provider": "aws",
                        "service": "rds",
                        "resource_id": "rds-1",
                        "name": "payments-db",
                        "region": "us-east-1",
                        "account_id": "prod-123",
                        "status": "available",
                        "public_exposure": True,
                        "tags": {"service": "payments"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def test_status_command() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Librarian scanning" in result.stdout
    assert "Watchman observability checks" in result.stdout
    assert "cloud environment assessment" in result.stdout
    assert "evidence plus observability guardrails" in result.stdout
    assert "chaos hardening suite" in result.stdout


def test_init_command_creates_workspace_files(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"

    result = runner.invoke(app, ["init", str(workspace)])

    assert result.exit_code == 0
    assert (workspace / "swarm.workspace.json").exists()
    assert (workspace / "cloud_catalog.sample.json").exists()
    assert (workspace / "incident_snapshot.sample.json").exists()


def test_connect_command_inventories_cloud_catalog(tmp_path: Path) -> None:
    runner = CliRunner()
    catalog_path = tmp_path / "catalog.json"
    _write_catalog(catalog_path)

    result = runner.invoke(
        app,
        [
            "connect",
            "aws",
            "--catalog-file",
            str(catalog_path),
            "--account-id",
            "prod-123",
            "--region",
            "us-east-1",
        ],
    )

    assert result.exit_code == 0
    assert "Resource count: 2" in result.stdout
    assert "Public resources:" in result.stdout


def test_deploy_command_runs_workflow_and_writes_artifacts(
    tmp_path: Path,
    python_fastapi_project: Path,
) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "artifacts"

    result = runner.invoke(
        app,
        ["deploy", str(python_fastapi_project), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 0
    assert "deployment_plan_ready" in result.stdout
    assert (output_dir / "Dockerfile").exists()
    assert (output_dir / "deployment.yaml").exists()
    assert (output_dir / ".github" / "workflows" / "generated-ci.yml").exists()


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


def test_chaos_command_runs_hardening_suite(
    python_fastapi_project: Path,
) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["chaos", str(python_fastapi_project), "--json"],
    )

    assert result.exit_code == 0
    assert '"total_scenarios": 5' in result.stdout
    assert '"failed_scenarios": 0' in result.stdout


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
