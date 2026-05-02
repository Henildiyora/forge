from __future__ import annotations

import typer


def status() -> None:
    typer.echo(
        "FORGE currently includes project indexing, a Manager-led build flow (project preview, "
        "ranked strategies, specialist agents + Captain review), strategy-based artifact "
        "generation, `forge ask` / `forge chat` / `forge explain`, Watchman monitoring, "
        "Kubernetes safety gates, approval checkpoints, cloud environment assessment, "
        "evidence-enforced LLM flows, and the hardening suite."
    )
