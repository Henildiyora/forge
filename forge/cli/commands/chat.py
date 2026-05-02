from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from forge.cli.commands import explain as explain_module
from forge.cli.commands.manager_context import load_manager_context
from forge.cli.runtime import cli_settings, run_async
from forge.core.llm import LLMClient


def chat(
    project_path: Annotated[
        Path | None,
        typer.Argument(exists=True, file_okay=False, dir_okay=True),
    ] = None,
) -> None:
    """Multi-turn chat with the FORGE Manager (type /exit to quit)."""

    root = project_path or Path.cwd()
    settings = cli_settings()
    llm = LLMClient(settings)
    typer.echo("FORGE Manager chat. Commands: /exit, /explain <file under .forge/generated/>")
    while True:
        try:
            line = typer.prompt("forge>")
        except typer.Abort:
            break
        text = (line or "").strip()
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            typer.echo("Goodbye.")
            break
        if text.startswith("/explain "):
            rel = text.split(" ", 1)[1].strip()
            try:
                explain_module.explain(rel, root)
            except typer.BadParameter as exc:
                typer.echo(str(exc))
            continue
        ctx = load_manager_context(root)
        prompt = (
            "FORGE_MANAGER_ANSWER_JSON\n"
            'Return JSON only: {"answer": string}.\n\n'
            f"CONTEXT:\n{ctx}\n\nUSER:\n{text}\n"
        )
        response = run_async(
            llm.complete(
                prompt=prompt,
                task_id=f"chat-{root.name}",
                agent="manager",
                expected_format="json",
            )
        )
        typer.echo(f"manager> {response.data.get('answer') or response.data.get('summary')}")
