from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge.cli.runtime import cli_settings
from forge.core.audit import AuditLog
from forge.core.workspace import ForgeWorkspace


def audit(
    project_path: Annotated[
        Path | None,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            help="Project to inspect. Defaults to the current directory.",
        ),
    ] = None,
    tail: Annotated[
        int,
        typer.Option(
            "--tail",
            "-n",
            help="Show only the last N entries. Use 0 to show every entry.",
        ),
    ] = 20,
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Print the JSON Lines verbatim."),
    ] = False,
) -> None:
    """Show actions FORGE has taken in this project.

    Reads ``<project>/.forge/audit.log``. The log is append-only and never
    rewritten, so missing entries indicate FORGE never recorded an action,
    not that an action was hidden.
    """

    settings = cli_settings()
    console = Console()
    workspace = ForgeWorkspace(project_path or Path.cwd(), settings)
    log_path = workspace.workspace_dir / "audit.log"

    if not log_path.exists():
        console.print(
            f"[yellow]No audit log at[/yellow] {log_path}. "
            "FORGE has not recorded any actions in this project yet."
        )
        raise typer.Exit(code=0)

    log = AuditLog(log_path)
    entries = log.tail(tail)

    if raw:
        for entry in entries:
            console.print(entry.model_dump_json())
        return

    table = Table(title=f"forge audit  ({log_path})", show_lines=False)
    table.add_column("Timestamp", style="dim")
    table.add_column("Actor", style="bold")
    table.add_column("Action")
    table.add_column("Target")
    table.add_column("Task")
    table.add_column("Evidence")
    for entry in entries:
        table.add_row(
            entry.timestamp,
            entry.actor,
            entry.action,
            entry.target,
            entry.task_id or "—",
            ", ".join(entry.evidence) if entry.evidence else "—",
        )
    console.print(table)
    console.print(
        f"\n{len(entries)} entry/entries shown. Total in log: {len(log.read_all())}.",
        style="dim",
    )
