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

    settings = get_settings()
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


@app.command("version")
def version_command() -> None:
    """Print package version."""

    from swarm import __version__

    console.print(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
