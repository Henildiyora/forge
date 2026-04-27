from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from swarm.cli.runtime import cli_settings, local_message_bus, run_async
from swarm.orchestrator.graph import build_swarm_graph
from swarm.orchestrator.state import SwarmState
from swarm.orchestrator.workflows.deploy_workflow import build_default_deploy_dependencies


def deploy(
    project_path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True),
    ],
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Optional directory where generated deployment artifacts will be written.",
        ),
    ] = None,
    max_iterations: Annotated[int, typer.Option(min=1, max=10)] = 3,
) -> None:
    """Run the deploy workflow against a local project path."""

    settings = cli_settings()
    bus = local_message_bus(settings)
    dependencies = build_default_deploy_dependencies(settings, bus)
    graph = build_swarm_graph(dependencies)
    state = SwarmState(
        task_id=f"deploy-{uuid4().hex[:8]}",
        workflow_type="deploy",
        project_path=str(project_path.resolve()),
        max_iterations=max_iterations,
    )
    result = run_async(graph.ainvoke(state))

    if result.current_step == "error":
        for error in result.errors:
            typer.echo(f"ERROR: {error}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Task: {result.task_id}")
    typer.echo(f"Current step: {result.current_step}")
    typer.echo(f"Framework: {result.project_metadata.get('framework', 'unknown')}")
    typer.echo(f"Language: {result.project_metadata.get('language', 'unknown')}")
    typer.echo(f"Port: {result.project_metadata.get('port', 'unknown')}")
    typer.echo(f"Completed steps: {', '.join(result.completed_steps)}")
    typer.echo(f"Summary: {result.deployment_summary or 'Deployment plan ready.'}")

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        if result.dockerfile is not None:
            (output_dir / "Dockerfile").write_text(result.dockerfile, encoding="utf-8")
        if result.docker_compose is not None:
            (output_dir / "docker-compose.generated.yml").write_text(
                result.docker_compose,
                encoding="utf-8",
            )
        for manifest_name, content in result.k8s_manifests.items():
            (output_dir / manifest_name).write_text(content, encoding="utf-8")
        if result.cicd_pipeline is not None:
            workflow_dir = output_dir / ".github" / "workflows"
            workflow_dir.mkdir(parents=True, exist_ok=True)
            (workflow_dir / "generated-ci.yml").write_text(
                result.cicd_pipeline,
                encoding="utf-8",
            )
        typer.echo(f"Artifacts written to: {output_dir.resolve()}")
    else:
        typer.echo("Artifacts are available in memory; pass --output-dir to write them to disk.")
