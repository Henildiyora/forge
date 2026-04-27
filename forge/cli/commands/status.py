from __future__ import annotations

import typer


def status() -> None:
    typer.echo(
        "FORGE currently includes project indexing, structured build conversations, "
        "strategy-based artifact generation, Watchman monitoring, Kubernetes safety gates, "
        "approval checkpoints, cloud environment assessment, evidence-enforced LLM flows, "
        "and the hardening suite."
    )
