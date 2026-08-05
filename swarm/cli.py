"""Typer CLI: ``swarm run`` and helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from swarm.config import REPO_ROOT, get_settings
from swarm.graph import run_swarm
from swarm.progress import RunRecorder
from swarm.runtime import SwarmRuntime
from swarm.schemas import SwarmConfig, SwarmState

app = typer.Typer(
    name="swarm",
    help="DevOps Swarm: three-agent LangGraph incident triage with Docker dry-run.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_FIXTURE = (
    REPO_ROOT / "benchmark" / "fixtures" / "commits" / "payment_timeout.json"
)


@app.command("run")
def run_command(
    service: str = typer.Option("checkout-api", help="Service label used in PromQL."),
    scenario: Optional[str] = typer.Option(
        None, help="Benchmark scenario id (loads fixtures + runtime_env)."
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Load LiveConfig from config.yaml (real service). Incompatible with --scenario/--offline.",
    ),
    commit_source: str = typer.Option(
        "fixture", help="'fixture' (deterministic) or 'live' (GitHub API)."
    ),
    commit_fixture: Optional[Path] = typer.Option(
        None, help="Path to a commit-history fixture JSON file."
    ),
    repository: str = typer.Option(
        "acme/checkout-api", help="owner/name used for live GitHub queries."
    ),
    lookback_minutes: int = typer.Option(60, help="Prometheus lookback window."),
    max_repair_attempts: int = typer.Option(
        0,
        help="Ops re-plans allowed after a failed dry-run (0 = human review immediately).",
    ),
    skip_llm: bool = typer.Option(
        False, help="Force the deterministic Ops planner even if an API key is set."
    ),
    offline: bool = typer.Option(
        False,
        help="Replay OpenMetrics fixtures in-process instead of querying Prometheus.",
    ),
    now: Optional[str] = typer.Option(
        None,
        help="ISO timestamp treated as 'now' for the monitoring window (benchmark use).",
    ),
) -> None:
    """Run the full Monitoring → Code Analysis → Ops → dry-run graph."""

    if live and (scenario or offline):
        console.print(
            "[red]--live cannot be combined with --scenario or --offline.[/red]\n"
            "Use `swarm run --live` for a real service, or "
            "`swarm run --scenario <name> --offline` for the demo."
        )
        raise typer.Exit(code=2)

    settings = get_settings()

    if live:
        _run_live(settings=settings, max_repair_attempts=max_repair_attempts, skip_llm=skip_llm)
        return

    runtime_env: dict[str, str] = {}
    service_paths = ["sandbox/target_service/", "checkout/", "payments/"]
    fixture_path = commit_fixture or DEFAULT_FIXTURE
    metrics_fixture: Path | None = None
    scenario_id = scenario

    if scenario:
        from benchmark.scenarios import ensure_fixtures, load_scenario

        ensure_fixtures()
        loaded = load_scenario(scenario)
        runtime_env = dict(loaded.runtime_env)
        service = loaded.service
        fixture_path = loaded.commit_fixture
        metrics_fixture = loaded.metrics_fixture
        service_paths = list(loaded.service_paths)
        repository = loaded.repository
        scenario_id = loaded.id
        if now is None:
            now = loaded.anchor_time.isoformat()

    if commit_source == "fixture" and not fixture_path.exists():
        console.print(
            f"[red]Commit fixture not found:[/red] {fixture_path}\n"
            "Pass --commit-fixture or run a --scenario that ships one."
        )
        raise typer.Exit(code=2)

    config = SwarmConfig(
        service=service,
        lookback_minutes=lookback_minutes,
        repository=repository,
        service_paths=service_paths,
        commit_source=commit_source,
        max_repair_attempts=max_repair_attempts,
        scenario_id=scenario_id,
        runtime_env=runtime_env,
    )
    state = SwarmState(config=config)
    recorder = RunRecorder(state.run_id, settings.runs_dir)

    prometheus_label = (
        f"fixture://{metrics_fixture}" if offline and metrics_fixture else settings.prometheus_url
    )
    console.print(
        Panel.fit(
            f"run_id=[bold]{state.run_id}[/bold]\n"
            f"service={service}  commit_source={commit_source}\n"
            f"prometheus={prometheus_label}\n"
            f"llm={'anthropic' if settings.has_anthropic and not skip_llm else 'heuristic'}\n"
            f"events={recorder.path}",
            title="DevOps Swarm",
        )
    )

    prometheus_client = None
    if offline:
        if metrics_fixture is None:
            console.print("[red]--offline requires --scenario so a metrics fixture is known.[/red]")
            raise typer.Exit(code=2)
        from swarm.tools.fixture_prometheus import FixturePrometheusClient

        prometheus_client = FixturePrometheusClient(metrics_fixture)

    runtime = SwarmRuntime(
        settings,
        commit_fixture=fixture_path if commit_source == "fixture" else None,
        commit_source_name=commit_source,
        prometheus_client=prometheus_client,
        skip_llm=skip_llm,
    )
    try:
        # Optional clock pin for seeded scenarios: MonitoringAgent accepts `now`.
        if now is not None:
            pinned = datetime.fromisoformat(now.replace("Z", "+00:00"))
            if pinned.tzinfo is None:
                pinned = pinned.replace(tzinfo=UTC)
            _install_clock(runtime, pinned)

        final = run_swarm(state, runtime, recorder)
    finally:
        runtime.close()

    _print_summary(final)
    out = settings.runs_dir / f"{final.run_id}.final.json"
    out.write_text(final.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"[dim]Wrote {out}[/dim]")
    raise typer.Exit(code=0 if final.status.value != "error" else 1)


@app.command("status")
def status_command(
    run_id: Optional[str] = typer.Argument(None, help="Run id; defaults to newest."),
) -> None:
    """Show node statuses from a run's event stream."""

    from swarm.progress import iter_runs, node_statuses, read_events

    settings = get_settings()
    if run_id:
        path = settings.runs_dir / f"{run_id}.jsonl"
    else:
        paths = list(iter_runs(settings.runs_dir))
        if not paths:
            console.print("No runs found.")
            raise typer.Exit(code=1)
        path = paths[0]
    if not path.exists():
        console.print(f"Run file not found: {path}")
        raise typer.Exit(code=1)
    events = read_events(path)
    statuses = node_statuses(events)
    table = Table(title=f"Run {path.stem}")
    table.add_column("Node")
    table.add_column("Status")
    for node, status in statuses.items():
        table.add_row(node, status)
    console.print(table)


def _install_clock(runtime: SwarmRuntime, pinned: datetime) -> None:
    """Pin MonitoringAgent's notion of 'now' for reproducible seeded runs."""

    original = runtime.monitoring.run

    def pinned_run(state, *, now=None):  # type: ignore[no-untyped-def]
        return original(state, now=pinned)

    runtime.monitoring.run = pinned_run  # type: ignore[method-assign]


def _print_summary(state: SwarmState) -> None:
    table = Table(title=f"Result: {state.status.value}")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("run_id", state.run_id)
    table.add_row("elapsed_s", f"{state.elapsed_seconds:.2f}")
    table.add_row("nodes", " → ".join(state.completed_nodes))
    if state.incident:
        table.add_row(
            "incident",
            f"{state.incident.severity.value} "
            f"{state.incident.spike_magnitude:.1f}x @ "
            f"{state.incident.start_timestamp.isoformat()}",
        )
    if state.commit_candidates:
        top = state.commit_candidates[0]
        table.add_row(
            "top_commit",
            f"{top.sha[:8]} score={top.relevance_score:.2f} {top.message}",
        )
    if state.proposed_fix:
        table.add_row(
            "fix",
            f"[{state.proposed_fix.source}] {state.proposed_fix.summary}",
        )
    if state.latest_dry_run:
        dr = state.latest_dry_run
        table.add_row(
            "dry_run",
            f"{'PASS' if dr.passed else 'FAIL'} attempt={dr.attempt} "
            f"({dr.duration_seconds:.1f}s)",
        )
    if state.human_review_reason:
        table.add_row("human_review", state.human_review_reason)
    if state.errors:
        table.add_row("errors", "; ".join(state.errors))
    console.print(table)


@app.command("init")
def init_command() -> None:
    """Interactively create config.yaml for ``swarm run --live``."""

    from swarm.init_flow import (
        resolve_path,
        validate_docker_build,
        validate_github,
        validate_health_endpoints,
        validate_prometheus,
    )
    from swarm.live import LiveConfig, LiveConfigError, save_live_config

    console.print(
        Panel.fit(
            "Connect DevOps Swarm to a real service.\n"
            "Each answer is validated immediately with real API/Docker output.\n"
            "Secrets stay in .env — never in config.yaml.",
            title="swarm init",
        )
    )

    prometheus_url = _prompt_until_ok(
        "Prometheus base URL",
        default="http://localhost:9090",
        example="http://localhost:9090",
        validate=lambda value: _check_prometheus_url(value),
    )
    error_query = _prompt_until_ok(
        "PromQL error-rate query",
        default="",
        example='rate(http_requests_total{code=~"5.."}[5m]) / rate(http_requests_total[5m])',
        hint=(
            f"Find metric names with:\n"
            f"  curl {prometheus_url}/api/v1/label/__name__/values"
        ),
        validate=lambda value: validate_prometheus(prometheus_url, value),
    )

    github_repo = _prompt_until_ok(
        "GitHub repository (owner/name)",
        default="",
        example="acme/checkout-api",
        validate=lambda value: value if "/" in value else (_raise("Use owner/name form")),
    )
    token = _prompt_secret("GitHub token (repo read scope)")
    _prompt_until_ok(
        "Confirm GitHub access",
        default="retry",
        example="press Enter to validate",
        validate=lambda _: validate_github(github_repo, token),
        skip_input=True,
    )

    service_name = _prompt_until_ok(
        "Service name (label)",
        default="checkout-api",
        example="checkout-api",
        validate=lambda value: value.strip() or _raise("service name required"),
    )
    paths_raw = _prompt_until_ok(
        "Service path prefixes for commit scoring (comma-separated)",
        default="./",
        example="src/,services/checkout/",
        validate=lambda value: value,
    )
    service_paths = [p.strip() for p in paths_raw.split(",") if p.strip()] or ["./"]

    dockerfile = _prompt_until_ok(
        "Path to Dockerfile",
        default="sandbox/target_service/Dockerfile",
        example="sandbox/target_service/Dockerfile",
        validate=lambda value: str(resolve_path(value)),
    )
    context = _prompt_until_ok(
        "Docker build context directory",
        default="sandbox/target_service",
        example="sandbox/target_service",
        validate=lambda value: str(resolve_path(value)),
    )
    dockerfile_path = resolve_path(dockerfile)
    context_path = resolve_path(context)
    image_tag = "swarm-init-check"
    _prompt_until_ok(
        "Confirm docker build",
        default="retry",
        example="press Enter to build",
        validate=lambda _: validate_docker_build(dockerfile_path, context_path, image_tag),
        skip_input=True,
    )

    endpoints_raw = _prompt_until_ok(
        "Health endpoints (comma-separated paths)",
        default="/healthz",
        example="/health,/ready",
        validate=lambda value: value,
    )
    endpoints = []
    for part in endpoints_raw.split(","):
        item = part.strip()
        if not item:
            continue
        if not item.startswith("/"):
            item = "/" + item
        endpoints.append(item)
    if not endpoints:
        console.print("[red]Need at least one endpoint[/red]")
        raise typer.Exit(code=2)

    container_port_raw = _prompt_until_ok(
        "Container listen port",
        default="8080",
        example="8080",
        validate=lambda value: value if value.isdigit() else _raise("port must be an integer"),
    )
    container_port = int(container_port_raw)

    _prompt_until_ok(
        "Confirm health endpoints",
        default="retry",
        example="press Enter to curl the running container",
        validate=lambda _: validate_health_endpoints(
            image_tag, endpoints, container_port=container_port
        ),
        skip_input=True,
    )

    allow_raw = _prompt_until_ok(
        "Allowed fix action kinds (comma-separated)",
        default="env_override,file_replace",
        example="env_override,file_replace",
        validate=lambda value: value,
    )
    allowlist = [a.strip() for a in allow_raw.split(",") if a.strip()] or [
        "env_override",
        "file_replace",
    ]

    try:
        config = LiveConfig(
            prometheus_url=prometheus_url,
            error_metric_query=error_query,
            github_repo=github_repo,
            service_name=service_name,
            service_paths=service_paths,
            service_health_endpoints=endpoints,
            service_dockerfile_path=str(dockerfile_path),
            service_build_context=str(context_path),
            fix_action_allowlist=allowlist,
            container_port=container_port,
        )
    except LiveConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Invalid config: {exc}[/red]")
        raise typer.Exit(code=2) from exc

    path = save_live_config(config)
    console.print(f"\n[green]Wrote {path}[/green] (secrets excluded)")
    console.print(
        Panel.fit(
            "Add these to your .env file (create it if needed):\n\n"
            f"GITHUB_TOKEN={token}\n"
            "# optional:\n"
            "# ANTHROPIC_API_KEY=\n"
            f"PROMETHEUS_URL={prometheus_url}\n\n"
            "Then run:\n"
            "  swarm run --live",
            title="Next steps",
        )
    )


def _raise(message: str) -> str:
    from swarm.live import LiveConfigError

    raise LiveConfigError(message)


def _check_prometheus_url(value: str) -> str:
    from swarm.live import LiveConfigError
    from swarm.tools.prometheus import PrometheusClient

    # URL-only readiness; full PromQL is validated in the next step.
    cleaned = value.strip().rstrip("/")
    if not cleaned.startswith(("http://", "https://")):
        raise LiveConfigError("URL must start with http:// or https://")
    client = PrometheusClient(base_url=cleaned, timeout_seconds=5.0)
    try:
        if not client.is_ready():
            raise LiveConfigError(
                f"Could not reach Prometheus at {cleaned}. "
                "Check the service is running and the port is correct."
            )
    finally:
        client.close()
    return f"Prometheus reachable at {cleaned}"


def _prompt_until_ok(
    label: str,
    *,
    default: str,
    example: str,
    validate,
    hint: str | None = None,
    skip_input: bool = False,
) -> str:
    from swarm.live import LiveConfigError

    while True:
        console.print(f"\n[bold]{label}[/bold]")
        console.print(f"[dim]Example: {example}[/dim]")
        if hint:
            console.print(f"[dim]{hint}[/dim]")
        if skip_input:
            raw = default
            console.print("[dim]Validating…[/dim]")
        else:
            raw = typer.prompt(label, default=default if default else None)
        try:
            summary = validate(raw)
            console.print(f"[green]{summary}[/green]")
            if skip_input:
                return raw
            if typer.confirm("Does this look right?", default=True):
                return raw.strip() if isinstance(raw, str) else raw
            console.print("[yellow]Okay — try again.[/yellow]")
        except LiveConfigError as exc:
            console.print(f"[red]{exc}[/red]")
            console.print("[yellow]Fix the value and retry this step.[/yellow]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Unexpected error: {exc}[/red]")
            console.print("[yellow]Fix the value and retry this step.[/yellow]")


def _prompt_secret(label: str) -> str:
    while True:
        value = typer.prompt(label, hide_input=True)
        if value.strip():
            return value.strip()
        console.print("[red]Token cannot be empty.[/red]")


def _run_live(*, settings, max_repair_attempts: int, skip_llm: bool) -> None:
    from swarm.live import LiveConfigError, load_live_config, require_github_token
    from swarm.tools.prometheus import PrometheusClient

    try:
        live_cfg = load_live_config(settings=settings)
        require_github_token(settings)
    except LiveConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    config = live_cfg.to_swarm_config(max_repair_attempts=max_repair_attempts)
    state = SwarmState(config=config)
    recorder = RunRecorder(state.run_id, settings.runs_dir)
    console.print(
        Panel.fit(
            f"run_id=[bold]{state.run_id}[/bold]\n"
            f"mode=live  service={config.service}\n"
            f"prometheus={live_cfg.prometheus_url}\n"
            f"repo={live_cfg.github_repo}\n"
            f"dockerfile={live_cfg.service_dockerfile_path}\n"
            f"llm={'anthropic' if settings.has_anthropic and not skip_llm else 'heuristic'}\n"
            f"events={recorder.path}",
            title="DevOps Swarm (live)",
        )
    )
    prometheus = PrometheusClient(
        base_url=live_cfg.prometheus_url,
        timeout_seconds=settings.prometheus_timeout_seconds,
    )
    runtime = SwarmRuntime(
        settings,
        commit_fixture=None,
        commit_source_name="live",
        prometheus_client=prometheus,
        skip_llm=skip_llm,
    )
    try:
        final = run_swarm(state, runtime, recorder)
    finally:
        runtime.close()
    _print_summary(final)
    out = settings.runs_dir / f"{final.run_id}.final.json"
    out.write_text(final.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"[dim]Wrote {out}[/dim]")
    raise typer.Exit(code=0 if final.status.value != "error" else 1)


@app.command("version")
def version_command() -> None:
    """Print package version."""

    from swarm import __version__

    console.print(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
