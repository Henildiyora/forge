from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from forge.cli.runtime import cli_settings, run_async
from forge.core.llm import LLMClient


def explain(
    relative_path: Annotated[
        str,
        typer.Argument(help="Path under .forge/generated/, e.g. Dockerfile or deployment.yaml"),
    ],
    project_path: Annotated[
        Path | None,
        typer.Argument(exists=True, file_okay=False, dir_okay=True),
    ] = None,
) -> None:
    """Plain-English explanation of a generated file (Manager)."""

    root = (project_path or Path.cwd()).resolve()
    file_path = (root / ".forge" / "generated" / relative_path).resolve()
    try:
        file_path.relative_to(root / ".forge" / "generated")
    except ValueError as exc:
        raise typer.BadParameter("Path must stay under .forge/generated/") from exc
    if not file_path.is_file():
        raise typer.BadParameter(f"File not found: {file_path}")
    snippet = file_path.read_text(encoding="utf-8")[:12000]
    settings = cli_settings()
    prompt = (
        "FORGE_MANAGER_ANSWER_JSON\n"
        "Return JSON only: {\"answer\": string} explaining this deployment file for a "
        "junior engineer: what it does, what to edit, and one common mistake.\n"
        f"FILE: {relative_path}\n\nCONTENT (truncated):\n{snippet}\n"
    )
    llm = LLMClient(settings)
    response = run_async(
        llm.complete(
            prompt=prompt,
            task_id=f"explain-{relative_path}",
            agent="manager",
            expected_format="json",
        )
    )
    typer.echo(str(response.data.get("answer") or response.data.get("summary")))
