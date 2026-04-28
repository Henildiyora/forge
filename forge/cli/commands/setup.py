from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console

from forge.cli.runtime import cli_settings
from forge.core.workspace import ConnectionProfile, ForgeWorkspace

RECOMMENDED_OLLAMA_MODEL = "qwen2.5-coder:1.5b"


def setup(
    project_path: Annotated[
        Path | None,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            help="Project to configure. Defaults to the current directory.",
        ),
    ] = None,
    backend: Annotated[
        str | None,
        typer.Option(
            "--backend",
            help="Force backend choice: heuristic, ollama, anthropic, openai, llamacpp.",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the model identifier."),
    ] = None,
) -> None:
    """One-shot wizard that picks a sensible LLM backend and saves the project profile.

    Detection order:
      1. ``--backend`` flag wins.
      2. Ollama is preferred when its API responds at the configured base URL.
      3. Otherwise the offline heuristic backend is selected so FORGE works without
         any API key or network.
    """

    settings = cli_settings()
    console = Console()
    workspace = ForgeWorkspace(project_path or Path.cwd(), settings)

    chosen_backend = backend
    chosen_model = model

    if chosen_backend is None:
        if _ollama_available(settings.ollama_base_url):
            chosen_backend = "ollama"
            chosen_model = chosen_model or RECOMMENDED_OLLAMA_MODEL
            console.print(
                f"[green]Detected Ollama[/green] at {settings.ollama_base_url}.",
            )
            console.print(
                f"  Recommended model: [bold]{chosen_model}[/bold] (small, free, runs locally).",
            )
            if shutil.which("ollama"):
                console.print(
                    f"  If you have not pulled it yet: [dim]ollama pull {chosen_model}[/dim]",
                )
        else:
            chosen_backend = "heuristic"
            chosen_model = chosen_model or "heuristic-builtin"
            console.print(
                "[yellow]Ollama not detected[/yellow]. Defaulting to the offline heuristic "
                "backend so FORGE works without any API key or network."
            )
            console.print(
                "  To enable natural-language Q&A later: install Ollama, then re-run "
                "[bold]forge setup[/bold]."
            )
    else:
        chosen_model = chosen_model or _default_model_for(chosen_backend, settings.llm_model)

    profile = ConnectionProfile(
        llm_backend=chosen_backend,
        llm_model=chosen_model,
        approval_transport="web",
        cloud_provider=None,
    )
    workspace.save_connection(profile)

    console.print()
    console.print(f"[bold]Saved profile[/bold] to {workspace.connection_path}")
    console.print(f"  Backend: {profile.llm_backend}")
    console.print(f"  Model:   {profile.llm_model}")
    console.print(f"  Approval transport: {profile.approval_transport}")
    console.print()
    console.print("Next: [bold]forge index[/bold] then [bold]forge build[/bold]")


def _ollama_available(base_url: str) -> bool:
    async def _probe() -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(base_url.rstrip("/") + "/api/version")
                return response.status_code < 400
        except Exception:
            return False

    try:
        return asyncio.run(_probe())
    except RuntimeError:
        return False


def _default_model_for(backend: str, fallback: str) -> str:
    if backend == "ollama":
        return RECOMMENDED_OLLAMA_MODEL
    if backend == "anthropic":
        return "claude-sonnet-4-20250514"
    if backend == "openai":
        return "gpt-4o"
    if backend == "llamacpp":
        return "local-gguf"
    if backend == "heuristic":
        return "heuristic-builtin"
    return fallback
