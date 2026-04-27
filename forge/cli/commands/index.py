from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from forge.agents.librarian.agent import LibrarianAgent
from forge.cli.runtime import cli_settings, local_message_bus, run_async
from forge.core.builds import index_project
from forge.core.workspace import ForgeWorkspace


def index(
    project_path: Annotated[
        Path | None,
        typer.Argument(exists=True, file_okay=False, dir_okay=True),
    ] = None,
) -> None:
    """Scan the current project and persist `.forge/index.json`."""

    resolved_project_path = project_path or Path.cwd()
    settings = cli_settings()
    workspace = ForgeWorkspace(resolved_project_path, settings)
    bus = local_message_bus(settings)
    librarian = LibrarianAgent(settings=settings, message_bus=bus)
    result = run_async(
        index_project(
            project_path=resolved_project_path,
            settings=settings,
            workspace=workspace,
            librarian=librarian,
        )
    )
    typer.echo(f"Indexed project: {result.project_path}")
    typer.echo(f"Language: {result.language}")
    typer.echo(f"Framework: {result.framework}")
    typer.echo(f"Service count: {result.service_count}")
    typer.echo(f"Workspace file: {workspace.index_path}")
