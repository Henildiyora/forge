from __future__ import annotations

import asyncio
import os
import shutil
import sys
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from forge.cli.runtime import cli_settings

_OK = "[green]OK[/green]"
_WARN = "[yellow]WARN[/yellow]"
_FAIL = "[red]FAIL[/red]"


def doctor(
    full: Annotated[
        bool,
        typer.Option(
            "--full/--quick",
            help="Run the full health probe (default) or a quick offline-only check.",
        ),
    ] = True,
    post_install: Annotated[
        bool,
        typer.Option(
            "--post-install",
            help="Print a first-run checklist after installing FORGE (PATH, forge, Docker).",
        ),
    ] = False,
) -> None:
    """Inspect the local FORGE environment and report each prerequisite.

    Renders a green/yellow/red status table covering Python, optional LLM
    backends, Kubernetes, sandbox tooling, and approval transports. Any non-OK
    result is informational; FORGE only requires Python and the heuristic
    backend to run a basic build.
    """

    settings = cli_settings()
    console = Console()
    table = Table(title="forge doctor", show_lines=False, expand=False)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail")

    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    table.add_row("Python >= 3.11", _OK, py)

    table.add_row(
        "Heuristic backend",
        _OK,
        "Always available. Works fully offline with no API key.",
    )

    if full:
        ollama_status, ollama_detail = _probe_http(settings.ollama_base_url + "/api/version")
        if ollama_status:
            table.add_row("Ollama", _OK, f"{settings.ollama_base_url} ({ollama_detail})")
        else:
            table.add_row("Ollama (optional)", _WARN, ollama_detail)
    else:
        table.add_row("Ollama (optional)", _WARN, "Skipped in --quick mode")

    if settings.anthropic_api_key:
        table.add_row("Anthropic API key", _OK, "Set via env")
    else:
        table.add_row("Anthropic API key (optional)", _WARN, "Not set; heuristic/Ollama still work")

    if shutil.which("kubectl"):
        table.add_row("kubectl", _OK, shutil.which("kubectl") or "")
    else:
        table.add_row("kubectl (optional)", _WARN, "Only needed for Kubernetes strategy")

    if shutil.which("docker"):
        table.add_row("docker", _OK, shutil.which("docker") or "")
    else:
        table.add_row("docker (optional)", _WARN, "Only needed for docker_compose strategy")

    local_bin = os.path.expanduser("~/.local/bin")
    path_entries = os.environ.get("PATH", "").split(":")
    if local_bin in path_entries:
        table.add_row("pipx PATH", _OK, f"{local_bin} is on PATH")
    else:
        table.add_row(
            "pipx PATH",
            _WARN,
            f"{local_bin} missing from PATH; run `pipx ensurepath` and restart shell",
        )

    if shutil.which("vcluster"):
        table.add_row("vcluster", _OK, shutil.which("vcluster") or "")
    else:
        table.add_row("vcluster (optional)", _WARN, "Only needed for K8s sandbox validation")

    if full:
        redis_ok, redis_detail = _probe_redis(settings.redis_url)
        if redis_ok:
            table.add_row("Redis", _OK, redis_detail)
        else:
            table.add_row("Redis (optional)", _WARN, redis_detail)
    else:
        table.add_row("Redis (optional)", _WARN, "Skipped in --quick mode")

    if settings.slack_signing_secret:
        table.add_row("Slack signing secret", _OK, "Set via env")
    else:
        table.add_row("Slack signing secret (optional)", _WARN, "Web fallback approval still works")

    console.print(table)
    if local_bin not in path_entries:
        # Plain echo so terminals/tests always see the remediation string (Rich tables can
        # format detail cells differently depending on width).
        typer.echo(
            "pipx PATH tip: ~/.local/bin is not on PATH — run pipx ensurepath, then restart your shell."
        )
    console.print(
        "\nFORGE works offline with no API key. Optional rows above only matter "
        "when you opt in to that integration.",
        style="dim",
    )
    if post_install:
        console.print(
            "\n[bold]Post-install checklist[/bold]\n"
            "1) Run: [cyan]which forge[/cyan] — if empty, run [cyan]pipx ensurepath[/cyan] "
            "and restart the terminal.\n"
            "2) Run: [cyan]forge --help[/cyan] — should list build, ask, chat, explain.\n"
            "3) For Docker workflows: start Docker Desktop (macOS) or the Docker daemon (Linux); "
            "then [cyan]docker info[/cyan].\n"
            "4) For Kubernetes sandbox: [cyan]brew install loft-sh/tap/vcluster[/cyan] (macOS) "
            "or install vcluster for your OS.\n"
            "5) In any project: [cyan]forge index[/cyan] then [cyan]forge build[/cyan].",
        )


def _probe_http(url: str) -> tuple[bool, str]:
    try:
        response = asyncio.run(_get(url))
    except Exception as exc:
        return False, f"unreachable ({type(exc).__name__})"
    if response.status_code >= 400:
        return False, f"status {response.status_code}"
    return True, "reachable"


async def _get(url: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=2.0) as client:
        return await client.get(url)


def _probe_redis(redis_url: str) -> tuple[bool, str]:
    try:
        from redis.asyncio import Redis
    except ImportError:
        return False, "redis package not installed"

    async def _ping() -> bool:
        client = Redis.from_url(redis_url)
        try:
            await client.ping()
            return True
        finally:
            await client.aclose()

    try:
        ok = asyncio.run(_ping())
        return (ok, redis_url) if ok else (False, "ping returned False")
    except Exception as exc:
        return False, f"unreachable ({type(exc).__name__})"
