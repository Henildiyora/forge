from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge.cli.runtime import cli_settings
from forge.core.workspace import ForgeWorkspace


def reset(
    project_path: Annotated[
        Path | None,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            help="Project to reset. Defaults to the current directory.",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip the confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Delete the project's ``.forge/`` directory.

    Removes index, session, connection profile, generated artifacts manifest,
    and the local audit log. Generated deployment files (Dockerfile, manifests,
    pipelines) are written outside ``.forge/`` and are NOT touched.
    """

    settings = cli_settings()
    console = Console()
    workspace = ForgeWorkspace(project_path or Path.cwd(), settings)

    if not workspace.workspace_dir.exists():
        console.print(
            f"[yellow]No .forge/ directory at[/yellow] {workspace.workspace_dir}; nothing to do."
        )
        raise typer.Exit(code=0)

    if not yes:
        confirmed = typer.confirm(
            f"Delete {workspace.workspace_dir}? This removes the project index and session.",
            default=False,
        )
        if not confirmed:
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(code=1)

    shutil.rmtree(workspace.workspace_dir)
    console.print(f"[green]Removed[/green] {workspace.workspace_dir}")
