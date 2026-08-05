"""Re-runnable benchmark: swarm wall-clock vs. documented manual baseline.

Produces ``benchmark/benchmark_results.json`` and ``.md``. The reduction
percentage is computed from the measured run — never hardcoded.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from benchmark.scenarios import Scenario, ensure_fixtures, list_scenarios
from swarm.config import REPO_ROOT, get_settings
from swarm.graph import run_swarm
from swarm.progress import RunRecorder
from swarm.runtime import SwarmRuntime
from swarm.schemas import SwarmConfig, SwarmState
from swarm.tools.fixture_prometheus import FixturePrometheusClient

RESULTS_JSON = REPO_ROOT / "benchmark" / "benchmark_results.json"
RESULTS_MD = REPO_ROOT / "benchmark" / "benchmark_results.md"


@dataclass
class ScenarioResult:
    scenario_id: str
    description: str
    manual_baseline_seconds: float
    swarm_seconds: float
    reduction_pct: float
    status: str
    dry_run_passed: bool | None
    run_id: str
    top_commit: str | None
    fix_summary: str | None
    nodes: list[str]


def _reduction(manual: float, swarm: float) -> float:
    if manual <= 0:
        return 0.0
    return max(0.0, (manual - swarm) / manual * 100.0)


def run_scenario(scenario: Scenario, *, offline: bool, skip_llm: bool) -> ScenarioResult:
    ensure_fixtures()
    settings = get_settings()
    config = SwarmConfig(
        service=scenario.service,
        lookback_minutes=60,
        repository=scenario.repository,
        service_paths=list(scenario.service_paths),
        commit_source="fixture",
        max_repair_attempts=0,
        scenario_id=scenario.id,
        runtime_env=dict(scenario.runtime_env),
        step_seconds=30,
    )
    state = SwarmState(config=config)
    recorder = RunRecorder(state.run_id, settings.runs_dir)

    prometheus = None
    if offline:
        prometheus = FixturePrometheusClient(scenario.metrics_fixture)

    runtime = SwarmRuntime(
        settings,
        commit_fixture=scenario.commit_fixture,
        commit_source_name="fixture",
        prometheus_client=prometheus,
        skip_llm=skip_llm,
    )

    # Pin the monitoring clock to the scenario anchor so the lookback window
    # lines up with the seeded (or fixture-replayed) metric data.
    original = runtime.monitoring.run

    def pinned(state_arg, *, now=None):  # type: ignore[no-untyped-def]
        return original(state_arg, now=scenario.anchor_time)

    runtime.monitoring.run = pinned  # type: ignore[method-assign]

    started = time.perf_counter()
    try:
        final = run_swarm(state, runtime, recorder)
    finally:
        runtime.close()
    elapsed = time.perf_counter() - started

    top = final.commit_candidates[0].sha if final.commit_candidates else None
    fix = final.proposed_fix.summary if final.proposed_fix else None
    dry = final.latest_dry_run.passed if final.latest_dry_run else None
    return ScenarioResult(
        scenario_id=scenario.id,
        description=scenario.description,
        manual_baseline_seconds=scenario.manual_baseline_seconds,
        swarm_seconds=round(elapsed, 3),
        reduction_pct=round(_reduction(scenario.manual_baseline_seconds, elapsed), 2),
        status=final.status.value,
        dry_run_passed=dry,
        run_id=final.run_id,
        top_commit=top,
        fix_summary=fix,
        nodes=list(final.completed_nodes),
    )


def write_report(results: list[ScenarioResult]) -> None:
    manual = [r.manual_baseline_seconds for r in results]
    swarm = [r.swarm_seconds for r in results]
    avg_manual = statistics.fmean(manual) if manual else 0.0
    avg_swarm = statistics.fmean(swarm) if swarm else 0.0
    avg_reduction = _reduction(avg_manual, avg_swarm)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "methodology": "benchmark/baseline_methodology.md",
        "disclaimer": (
            "Benchmark scenarios use seeded Prometheus data and fixture commit "
            "histories — not production traffic. Manual baselines are documented "
            "per-step estimates, not a controlled human study."
        ),
        "summary": {
            "scenario_count": len(results),
            "avg_manual_seconds": round(avg_manual, 3),
            "avg_swarm_seconds": round(avg_swarm, 3),
            "avg_reduction_pct": round(avg_reduction, 2),
        },
        "scenarios": [asdict(r) for r in results],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Benchmark results",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        f"> {payload['disclaimer']}",
        "",
        "## Summary",
        "",
        f"- Scenarios: **{len(results)}**",
        f"- Average manual triage baseline: **{avg_manual:.1f}s**",
        f"- Average swarm end-to-end: **{avg_swarm:.1f}s**",
        f"- Average reduction: **{avg_reduction:.1f}%**",
        "",
        "Reduction is recomputed from this run's measurements. See "
        "`baseline_methodology.md` for how the manual numbers were obtained.",
        "",
        "## Per scenario",
        "",
        "| Scenario | Manual (s) | Swarm (s) | Reduction | Status | Dry-run |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for r in results:
        dry = (
            "pass"
            if r.dry_run_passed is True
            else "fail"
            if r.dry_run_passed is False
            else "n/a"
        )
        lines.append(
            f"| {r.scenario_id} | {r.manual_baseline_seconds:.0f} | "
            f"{r.swarm_seconds:.1f} | {r.reduction_pct:.1f}% | {r.status} | {dry} |"
        )
    lines.extend(["", "## Notes", ""])
    for r in results:
        lines.append(f"### {r.scenario_id}")
        lines.append("")
        lines.append(r.description)
        lines.append("")
        lines.append(f"- run_id: `{r.run_id}`")
        lines.append(f"- nodes: {' → '.join(r.nodes)}")
        if r.top_commit:
            lines.append(f"- top commit: `{r.top_commit[:12]}`")
        if r.fix_summary:
            lines.append(f"- fix: {r.fix_summary}")
        lines.append("")
    RESULTS_MD.write_text("\n".join(lines), encoding="utf-8")
    print(RESULTS_MD.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Replay OpenMetrics fixtures in-process instead of querying Prometheus.",
    )
    parser.add_argument(
        "--live-prometheus",
        action="store_true",
        help="Seed and query the docker-compose Prometheus (default when not --offline).",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        default=True,
        help="Use the deterministic Ops planner (default: on, for reproducibility).",
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Allow Anthropic when ANTHROPIC_API_KEY is set.",
    )
    parser.add_argument("--scenario", action="append", help="Limit to scenario id(s).")
    args = parser.parse_args(argv)

    ensure_fixtures()
    offline = args.offline or not args.live_prometheus
    if not offline:
        from benchmark.seed_prometheus import seed_scenario

        for scenario in list_scenarios():
            if args.scenario and scenario.id not in args.scenario:
                continue
            seed_scenario(scenario.id)

    skip_llm = not args.with_llm
    results: list[ScenarioResult] = []
    for scenario in list_scenarios():
        if args.scenario and scenario.id not in args.scenario:
            continue
        print(f"\n=== Running scenario {scenario.id} (offline={offline}) ===")
        result = run_scenario(scenario, offline=offline, skip_llm=skip_llm)
        print(
            f"{scenario.id}: swarm={result.swarm_seconds:.1f}s "
            f"manual={result.manual_baseline_seconds:.0f}s "
            f"reduction={result.reduction_pct:.1f}% status={result.status}"
        )
        results.append(result)

    write_report(results)
    print(f"\nWrote {RESULTS_JSON}")
    print(f"Wrote {RESULTS_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
