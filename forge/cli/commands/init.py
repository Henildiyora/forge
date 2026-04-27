from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer


def init_project(
    destination: Annotated[Path, typer.Argument(file_okay=False, dir_okay=True)],
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite existing generated files."),
    ] = False,
) -> None:
    """Create a local CLI workspace with sample inputs for the current forge surface."""

    destination.mkdir(parents=True, exist_ok=True)
    files = {
        "forge.workspace.json": _workspace_config(),
        "cloud_catalog.sample.json": _cloud_catalog(),
        "incident_snapshot.sample.json": _incident_snapshot(),
    }
    written: list[str] = []
    for relative_name, content in files.items():
        path = destination / relative_name
        if path.exists() and not force:
            raise typer.BadParameter(
                f"{relative_name} already exists in {destination}; use --force to overwrite"
            )
        path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
        written.append(relative_name)
    typer.echo(f"Initialized workspace at: {destination.resolve()}")
    typer.echo(f"Created files: {', '.join(written)}")


def _workspace_config() -> dict[str, object]:
    return {
        "provider": "aws",
        "account_id": "prod-123",
        "region": "us-east-1",
        "service": "payments",
        "namespace": "devops-forge",
    }


def _cloud_catalog() -> dict[str, list[dict[str, object]]]:
    return {
        "aws": [
            {
                "provider": "aws",
                "service": "eks",
                "resource_id": "eks-cluster-1",
                "name": "payments-eks",
                "region": "us-east-1",
                "account_id": "prod-123",
                "status": "active",
                "tags": {"service": "payments", "env": "prod"},
            },
            {
                "provider": "aws",
                "service": "ecr",
                "resource_id": "ecr-1",
                "name": "payments-registry",
                "region": "us-east-1",
                "account_id": "prod-123",
                "status": "available",
                "tags": {"service": "payments"},
            },
        ]
    }


def _incident_snapshot() -> dict[str, object]:
    return {
        "service": "payments",
        "namespace": "devops-forge",
        "anomalies": ["high error rate", "latency spike"],
        "error_rate": 0.12,
        "latency_p95_ms": 910.0,
        "restart_count": 1.0,
        "error_log_count": 4,
        "evidence": [
            "Observed error rate 0.120.",
            "Observed p95 latency 910.0 ms.",
        ],
        "confidence": 0.92,
    }
