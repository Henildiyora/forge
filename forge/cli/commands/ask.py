from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from forge.cli.commands.manager_context import load_manager_context
from forge.cli.runtime import cli_settings, run_async
from forge.core.llm import LLMClient


def ask(
    question: Annotated[str, typer.Argument(help="Question for the FORGE Manager.")],
    project_path: Annotated[
        Path | None,
        typer.Argument(exists=True, file_okay=False, dir_okay=True),
    ] = None,
) -> None:
    """Ask the Manager about your project, last build, or generated files."""

    root = project_path or Path.cwd()
    settings = cli_settings()
    ctx = load_manager_context(root)
    prompt = (
        "FORGE_MANAGER_ANSWER_JSON\n"
        'Return JSON only with keys: "answer" (string, plain language for a beginner), '
        '"references" (array of short strings citing filenames or docs).\n\n'
        f"CONTEXT:\n{ctx}\n\nQUESTION:\n{question}\n"
    )
    llm = LLMClient(settings)
    response = run_async(
        llm.complete(
            prompt=prompt,
            task_id=f"ask-{root.name}",
            agent="manager",
            expected_format="json",
        )
    )
    answer = response.data.get("answer") or response.data.get("summary")
    typer.echo(str(answer))
    refs = response.data.get("references")
    if isinstance(refs, list) and refs:
        typer.echo("\nReferences:")
        for ref in refs:
            typer.echo(f"  - {ref}")
