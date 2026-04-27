from __future__ import annotations

import typer


def status() -> None:
    typer.echo(
        "DevOps Swarm currently includes the core foundation, Librarian scanning, "
        "Captain deploy orchestration, config generation specialists, "
        "Watchman observability checks, Kubernetes runtime helpers, "
        "sandbox validation, incident approval triage, cloud environment assessment, "
        "a working operator CLI, evidence plus observability guardrails, "
        "and a Sprint 12 chaos hardening suite."
    )
