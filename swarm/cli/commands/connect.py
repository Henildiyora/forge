from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer

from swarm.agents.cloud_specialist.agent import CloudSpecialistAgent
from swarm.agents.cloud_specialist.mcp_client import MCPClient
from swarm.cli.runtime import cli_settings, local_message_bus, run_async


def connect(
    provider: Annotated[str, typer.Argument(help="Cloud provider: aws, gcp, or azure.")],
    catalog_file: Annotated[
        Path | None,
        typer.Option(
            "--catalog-file",
            help="Path to a JSON resource catalog used for local MCP-style cloud analysis.",
        ),
    ] = None,
    account_id: Annotated[str | None, typer.Option("--account-id")] = None,
    region: Annotated[str | None, typer.Option("--region")] = None,
    target_service: Annotated[
        str | None,
        typer.Option(
            "--target-service",
            help=(
                "If set, assess deployment readiness for this service "
                "instead of only listing resources."
            ),
        ),
    ] = None,
) -> None:
    """Inspect or assess a cloud environment through the Cloud Specialist."""

    catalog = _load_catalog(catalog_file)
    settings = cli_settings()
    bus = local_message_bus(settings)
    agent = CloudSpecialistAgent(
        settings=settings,
        message_bus=bus,
        mcp_client=MCPClient(resource_catalog=catalog),
    )

    if target_service is None:
        result = run_async(
            agent.inventory_environment(
                provider=_provider(provider),
                account_id=account_id,
                region=region,
            )
        )
        typer.echo(f"Provider: {result.provider}")
        typer.echo(f"Resource count: {result.summary.resource_count if result.summary else 0}")
        if result.summary is not None:
            typer.echo(f"Services: {result.summary.resources_by_service}")
            typer.echo(f"Regions: {result.summary.resources_by_region}")
            if result.summary.public_resources:
                typer.echo(f"Public resources: {', '.join(result.summary.public_resources)}")
        return

    result = run_async(
        agent.assess_environment(
            provider=_provider(provider),
            target_service=target_service,
            account_id=account_id,
            region=region,
        )
    )
    typer.echo(f"Provider: {result.provider}")
    typer.echo(f"Target service: {target_service}")
    if result.assessment is not None:
        typer.echo(f"Readiness score: {result.assessment.readiness_score:.2f}")
        typer.echo(f"Matched resources: {', '.join(result.assessment.matched_resources) or 'none'}")
        typer.echo(f"Blockers: {result.assessment.blockers or ['none']}")
        typer.echo(f"Recommendations: {result.assessment.recommendations or ['none']}")


def _load_catalog(catalog_file: Path | None) -> dict[str, list[dict[str, object]]]:
    if catalog_file is None:
        return {}
    if not catalog_file.exists():
        raise typer.BadParameter(f"catalog file does not exist: {catalog_file}")
    raw = json.loads(catalog_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise typer.BadParameter("catalog file must contain a JSON object keyed by provider")
    normalized: dict[str, list[dict[str, object]]] = {}
    for provider, resources in raw.items():
        if isinstance(provider, str) and isinstance(resources, list):
            normalized[provider] = [
                resource for resource in resources if isinstance(resource, dict)
            ]
    return normalized


def _provider(provider: str) -> Literal["aws", "gcp", "azure"]:
    if provider == "aws":
        return "aws"
    if provider == "gcp":
        return "gcp"
    if provider == "azure":
        return "azure"
    else:
        raise typer.BadParameter("provider must be one of: aws, gcp, azure")
