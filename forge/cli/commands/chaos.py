from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from forge.cli.runtime import cli_settings, run_async
from forge.core.hardening import run_hardening_suite
from forge.core.logging import configure_logging


def chaos(
    project_path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True),
    ],
    max_iterations: Annotated[int, typer.Option(min=1, max=10)] = 3,
    output_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Render the hardening report as JSON instead of a human summary.",
        ),
    ] = False,
) -> None:
    """Run the Sprint 12 hardening suite against a local project."""

    settings = cli_settings().model_copy(update={"log_level": "CRITICAL"})
    configure_logging(settings)
    report = run_async(
        run_hardening_suite(
            settings=settings,
            project_path=project_path.resolve(),
            max_iterations=max_iterations,
        )
    )

    if output_json:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        typer.echo(f"Project: {report.project_path}")
        typer.echo(f"Hardening readiness score: {report.readiness_score:.0%}")
        typer.echo(f"Passed scenarios: {report.passed_scenarios}/{report.total_scenarios}")
        typer.echo(f"Observed workflow runs: {report.observability.total_runs}")
        for scenario in report.scenarios:
            status = "PASS" if scenario.passed else "FAIL"
            typer.echo(f"[{status}] {scenario.name}: {scenario.observed_step}")
        typer.echo("Recommendations:")
        for recommendation in report.recommendations:
            typer.echo(f"- {recommendation}")

    if report.failed_scenarios > 0:
        raise typer.Exit(code=1)
