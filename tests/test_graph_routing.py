"""Graph branching: no-incident, ready-to-apply, needs-human-review."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from benchmark.scenarios import ensure_fixtures, load_scenario
from swarm.config import Settings
from swarm.dryrun.docker_sandbox import DockerSandbox
from swarm.graph import run_swarm
from swarm.progress import NullRecorder
from swarm.runtime import SwarmRuntime
from swarm.schemas import (
    DryRunResult,
    FixAction,
    FixActionKind,
    ProposedFix,
    RiskLevel,
    RunStatus,
    SandboxCheck,
    SwarmConfig,
    SwarmState,
)
from swarm.tools.fixture_prometheus import FixturePrometheusClient
from swarm.tools.prometheus import MetricSample, QueryRangeArgs, QueryRangeResult, MetricSeries


class FlatPrometheus:
    """Always returns a flat healthy series → no incident."""

    base_url = "fixture://flat"

    def query_range(self, args: QueryRangeArgs) -> QueryRangeResult:
        start = args.start
        samples = [
            MetricSample(timestamp=start + timedelta(seconds=30 * i), value=0.01)
            for i in range(40)
        ]
        return QueryRangeResult(
            query=args.query,
            series=[MetricSeries(samples=samples)],
            endpoint=f"{self.base_url}/query_range",
        )

    def close(self) -> None:
        return None


class RecordingSandbox(DockerSandbox):
    """Sandbox stub that returns a scripted pass/fail without Docker."""

    def __init__(self, settings: Settings, *, pass_on_attempt: int | None = 1) -> None:
        super().__init__(settings)
        self.pass_on_attempt = pass_on_attempt
        self.calls = 0

    def validate(self, fix, config, *, attempt: int = 1):  # type: ignore[no-untyped-def]
        self.calls += 1
        passed = self.pass_on_attempt is not None and attempt >= self.pass_on_attempt
        return DryRunResult(
            passed=passed,
            method="stub",
            attempt=attempt,
            exit_code=0 if passed else 1,
            duration_seconds=0.01,
            checks=[SandboxCheck(name="stub", passed=passed, detail="scripted")],
            logs="stub",
            rejection_reason=None if passed else "scripted_failure",
            image_tag="stub",
        )


@pytest.fixture(scope="module", autouse=True)
def _fixtures() -> None:
    ensure_fixtures()


def test_no_incident_branch(tmp_path: Path):
    settings = Settings(runs_dir=tmp_path)
    scenario = load_scenario("payment_timeout")
    runtime = SwarmRuntime(
        settings,
        commit_fixture=scenario.commit_fixture,
        commit_source_name="fixture",
        prometheus_client=FlatPrometheus(),  # type: ignore[arg-type]
        sandbox=RecordingSandbox(settings),
        skip_llm=True,
    )
    state = SwarmState(
        config=SwarmConfig(
            service=scenario.service,
            commit_source="fixture",
            repository=scenario.repository,
            runtime_env=dict(scenario.runtime_env),
            max_repair_attempts=0,
        )
    )
    final = run_swarm(state, runtime, NullRecorder())
    assert final.status == RunStatus.NO_INCIDENT
    assert "no_incident" in final.completed_nodes
    assert final.incident is None


def test_ready_to_apply_on_dry_run_pass(tmp_path: Path):
    settings = Settings(runs_dir=tmp_path)
    scenario = load_scenario("payment_timeout")
    runtime = SwarmRuntime(
        settings,
        commit_fixture=scenario.commit_fixture,
        commit_source_name="fixture",
        prometheus_client=FixturePrometheusClient(scenario.metrics_fixture),
        sandbox=RecordingSandbox(settings, pass_on_attempt=1),
        skip_llm=True,
    )
    state = SwarmState(
        config=SwarmConfig(
            service=scenario.service,
            lookback_minutes=60,
            commit_source="fixture",
            repository=scenario.repository,
            service_paths=list(scenario.service_paths),
            runtime_env=dict(scenario.runtime_env),
            max_repair_attempts=0,
            scenario_id=scenario.id,
        )
    )
    original = runtime.monitoring.run

    def pinned(state_arg, *, now=None):  # type: ignore[no-untyped-def]
        return original(state_arg, now=scenario.anchor_time)

    runtime.monitoring.run = pinned  # type: ignore[method-assign]
    final = run_swarm(state, runtime, NullRecorder())
    assert final.incident is not None
    assert final.commit_candidates
    assert final.proposed_fix is not None
    assert final.status == RunStatus.READY_TO_APPLY
    assert final.latest_dry_run is not None and final.latest_dry_run.passed


def test_human_review_when_dry_run_fails_and_no_repairs(tmp_path: Path):
    settings = Settings(runs_dir=tmp_path)
    scenario = load_scenario("payment_timeout")
    runtime = SwarmRuntime(
        settings,
        commit_fixture=scenario.commit_fixture,
        commit_source_name="fixture",
        prometheus_client=FixturePrometheusClient(scenario.metrics_fixture),
        sandbox=RecordingSandbox(settings, pass_on_attempt=None),
        skip_llm=True,
    )
    state = SwarmState(
        config=SwarmConfig(
            service=scenario.service,
            lookback_minutes=60,
            commit_source="fixture",
            repository=scenario.repository,
            service_paths=list(scenario.service_paths),
            runtime_env=dict(scenario.runtime_env),
            max_repair_attempts=0,
            scenario_id=scenario.id,
        )
    )
    original = runtime.monitoring.run

    def pinned(state_arg, *, now=None):  # type: ignore[no-untyped-def]
        return original(state_arg, now=scenario.anchor_time)

    runtime.monitoring.run = pinned  # type: ignore[method-assign]
    final = run_swarm(state, runtime, NullRecorder())
    assert final.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert final.human_review_reason
    assert final.latest_dry_run is not None
    assert final.latest_dry_run.passed is False
