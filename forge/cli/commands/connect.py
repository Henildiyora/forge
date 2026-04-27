from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from forge.cli.runtime import cli_settings
from forge.core.workspace import ConnectionProfile, ForgeWorkspace


def connect(
    project_path: Annotated[
        Path | None,
        typer.Argument(exists=True, file_okay=False, dir_okay=True),
    ] = None,
    backend: Annotated[
        str | None,
        typer.Option(
            "--backend",
            help="LLM backend: anthropic, openai, ollama, llamacpp, or heuristic.",
        ),
    ] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    approval_transport: Annotated[
        str,
        typer.Option("--approval-transport", help="Approval transport: web or slack."),
    ] = "web",
    cloud_provider: Annotated[
        str | None,
        typer.Option("--cloud-provider", help="Preferred cloud provider: aws, gcp, azure."),
    ] = None,
) -> None:
    """Configure project-local FORGE backend and approval preferences."""

    settings = cli_settings()
    workspace = ForgeWorkspace(project_path or Path.cwd(), settings)
    previous = workspace.load_connection()
    profile = ConnectionProfile(
        llm_backend=(
            backend
            or (previous.llm_backend if previous is not None else settings.llm_backend)
        ),
        llm_model=(
            model or (previous.llm_model if previous is not None else settings.llm_model)
        ),
        approval_transport=approval_transport if approval_transport else "web",
        cloud_provider=(
            cloud_provider
            or (previous.cloud_provider if previous is not None else None)
        ),
    )
    workspace.save_connection(profile)
    typer.echo(f"Saved FORGE connection settings in: {workspace.connection_path}")
    typer.echo(f"Backend: {profile.llm_backend}")
    typer.echo(f"Model: {profile.llm_model}")
    typer.echo(f"Approval transport: {profile.approval_transport}")
    typer.echo(f"Preferred cloud: {profile.cloud_provider or 'not set'}")
