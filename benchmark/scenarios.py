"""Seeded incident scenarios used by the benchmark and ``swarm run --scenario``.

These are clearly labelled benchmark fixtures — not production traffic.
Metric series are written as OpenMetrics and backfilled into a real Prometheus
TSDB; commit histories are JSON fixtures replayed by FixtureCommitSource.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from swarm.config import REPO_ROOT

FIXTURES = REPO_ROOT / "benchmark" / "fixtures"


@dataclass(frozen=True)
class Scenario:
    """One reproducible incident the swarm can triage end-to-end."""

    id: str
    description: str
    service: str
    repository: str
    # Wall-clock "now" for the monitoring lookback window.
    anchor_time: datetime
    # Minutes before anchor when the error-ratio spike begins.
    spike_start_offset_minutes: int
    # Broken runtime env the service (and dry-run sandbox) boots with.
    runtime_env: dict[str, str]
    # Healthy env the Ops Agent should restore toward.
    healthy_env: dict[str, str]
    commit_fixture: Path
    metrics_fixture: Path
    service_paths: list[str] = field(
        default_factory=lambda: ["sandbox/target_service/", "checkout/", "payments/"]
    )
    # Documented manual-triage baseline seconds for this scenario.
    manual_baseline_seconds: float = 900.0


def _anchor(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


SCENARIOS: dict[str, Scenario] = {
    "payment_timeout": Scenario(
        id="payment_timeout",
        description=(
            "PAYMENT_TIMEOUT_MS was lowered to 50ms by commit a1b2c3d4, causing a "
            "5xx error-ratio spike on checkout-api."
        ),
        service="checkout-api",
        repository="acme/checkout-api",
        anchor_time=_anchor("2026-08-04T18:00:00"),
        spike_start_offset_minutes=25,
        runtime_env={
            "PAYMENT_TIMEOUT_MS": "50",
            "FEATURE_CHECKOUT_V2": "false",
            "MAX_RETRIES": "3",
        },
        healthy_env={
            "PAYMENT_TIMEOUT_MS": "2000",
            "FEATURE_CHECKOUT_V2": "false",
            "MAX_RETRIES": "3",
        },
        commit_fixture=FIXTURES / "commits" / "payment_timeout.json",
        metrics_fixture=FIXTURES / "metrics" / "payment_timeout.openmetrics",
        manual_baseline_seconds=960.0,
    ),
    "feature_flag_blowup": Scenario(
        id="feature_flag_blowup",
        description=(
            "FEATURE_CHECKOUT_V2 flipped on without backend support, spiking 5xx."
        ),
        service="checkout-api",
        repository="acme/checkout-api",
        anchor_time=_anchor("2026-08-04T19:00:00"),
        spike_start_offset_minutes=20,
        runtime_env={
            "PAYMENT_TIMEOUT_MS": "2000",
            "FEATURE_CHECKOUT_V2": "true",
            "MAX_RETRIES": "3",
        },
        healthy_env={
            "PAYMENT_TIMEOUT_MS": "2000",
            "FEATURE_CHECKOUT_V2": "false",
            "MAX_RETRIES": "3",
        },
        commit_fixture=FIXTURES / "commits" / "feature_flag_blowup.json",
        metrics_fixture=FIXTURES / "metrics" / "feature_flag_blowup.openmetrics",
        manual_baseline_seconds=840.0,
    ),
    "retries_zeroed": Scenario(
        id="retries_zeroed",
        description="MAX_RETRIES set to 0, turning transient upstream blips into 5xx.",
        service="checkout-api",
        repository="acme/checkout-api",
        anchor_time=_anchor("2026-08-04T20:00:00"),
        spike_start_offset_minutes=30,
        runtime_env={
            "PAYMENT_TIMEOUT_MS": "2000",
            "FEATURE_CHECKOUT_V2": "false",
            "MAX_RETRIES": "0",
        },
        healthy_env={
            "PAYMENT_TIMEOUT_MS": "2000",
            "FEATURE_CHECKOUT_V2": "false",
            "MAX_RETRIES": "3",
        },
        commit_fixture=FIXTURES / "commits" / "retries_zeroed.json",
        metrics_fixture=FIXTURES / "metrics" / "retries_zeroed.openmetrics",
        manual_baseline_seconds=780.0,
    ),
}


def load_scenario(scenario_id: str) -> Scenario:
    """Look up a scenario by id or raise KeyError with available names."""

    if scenario_id not in SCENARIOS:
        known = ", ".join(sorted(SCENARIOS))
        raise KeyError(f"unknown scenario {scenario_id!r}; known: {known}")
    return SCENARIOS[scenario_id]


def list_scenarios() -> list[Scenario]:
    return [SCENARIOS[key] for key in sorted(SCENARIOS)]


def generate_openmetrics(scenario: Scenario, out: Path | None = None) -> Path:
    """Write an OpenMetrics fixture for the scenario's error-ratio shape.

    Baseline: ~1% 5xx for the leading window. Spike: ~40% 5xx after
    ``spike_start_offset_minutes``. Counters increase every 30s so ``rate()``
    is well-defined after backfill into Prometheus.
    """

    out = out or scenario.metrics_fixture
    out.parent.mkdir(parents=True, exist_ok=True)

    end = scenario.anchor_time
    start = end - timedelta(minutes=60)
    spike_at = end - timedelta(minutes=scenario.spike_start_offset_minutes)
    step = timedelta(seconds=30)

    # Build samples first, then emit one series at a time. promtool's OpenMetrics
    # importer creates cleaner blocks when a series is contiguous.
    ok_points: list[tuple[int, float]] = []
    err_points: list[tuple[int, float]] = []
    ok = 0.0
    err = 0.0
    ts = start
    while ts <= end:
        if ts >= spike_at:
            ok += 60.0
            err += 40.0
        else:
            ok += 99.0
            err += 1.0
        # promtool v2.53 treats OpenMetrics timestamps as seconds (then stores ms).
        seconds = int(ts.timestamp())
        ok_points.append((seconds, ok))
        err_points.append((seconds, err))
        ts += step

    labels_ok = f'service="{scenario.service}",status="2xx",job="seed"'
    labels_err = f'service="{scenario.service}",status="5xx",job="seed"'
    lines = [
        "# HELP http_requests_total Seeded benchmark fixture (not production).",
        "# TYPE http_requests_total counter",
    ]
    for seconds, value in ok_points:
        lines.append(f"http_requests_total{{{labels_ok}}} {value:.0f} {seconds}")
    for seconds, value in err_points:
        lines.append(f"http_requests_total{{{labels_err}}} {value:.0f} {seconds}")
    lines.append("# EOF")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def generate_commit_fixture(scenario: Scenario) -> Path:
    """Write a labelled commit-history fixture around the incident window."""

    out = scenario.commit_fixture
    out.parent.mkdir(parents=True, exist_ok=True)
    spike = scenario.anchor_time - timedelta(minutes=scenario.spike_start_offset_minutes)

    # Culprit lands ~8 minutes before the spike.
    culprit_time = spike - timedelta(minutes=8)
    decoys = [
        (culprit_time - timedelta(minutes=40), "docs: update runbook", ["docs/runbook.md"]),
        (culprit_time - timedelta(minutes=25), "chore: bump CI timeout", [".github/workflows/ci.yml"]),
        (culprit_time - timedelta(minutes=15), "refactor: tidy logging", ["checkout/logging.py"]),
    ]

    if scenario.id == "payment_timeout":
        culprit = {
            "sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            "author": "alex.chen",
            "message": "perf: lower PAYMENT_TIMEOUT_MS to reduce tail latency",
            "files_changed": [
                "sandbox/target_service/app.py",
                "checkout/config.yaml",
                "payments/client.py",
            ],
        }
    elif scenario.id == "feature_flag_blowup":
        culprit = {
            "sha": "b2c3d4e5f60718293a4b5c6d7e8f90123456789a",
            "author": "sam.ortiz",
            "message": "feat: enable FEATURE_CHECKOUT_V2 for 10% of traffic",
            "files_changed": [
                "sandbox/target_service/app.py",
                "checkout/flags.yaml",
            ],
        }
    else:
        culprit = {
            "sha": "c3d4e5f60718293a4b5c6d7e8f90123456789abc",
            "author": "jordan.lee",
            "message": "fix: set MAX_RETRIES=0 to fail fast on payment errors",
            "files_changed": [
                "sandbox/target_service/app.py",
                "payments/retry.py",
            ],
        }

    commits = []
    for index, (ts, message, files) in enumerate(decoys):
        commits.append(
            {
                "sha": f"d{index:02d}{'e' * 37}",
                "author": "bot",
                "message": message,
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "files_changed": files,
                "url": f"https://github.com/{scenario.repository}/commit/decoy{index}",
            }
        )
    commits.append(
        {
            **culprit,
            "timestamp": culprit_time.isoformat().replace("+00:00", "Z"),
            "url": f"https://github.com/{scenario.repository}/commit/{culprit['sha'][:8]}",
        }
    )
    # A post-incident commit that should score low.
    commits.append(
        {
            "sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            "author": "oncall",
            "message": "chore: page oncall about checkout errors",
            "timestamp": (spike + timedelta(minutes=3)).isoformat().replace("+00:00", "Z"),
            "files_changed": ["docs/incident.md"],
            "url": f"https://github.com/{scenario.repository}/commit/eeeeeeee",
        }
    )

    import json

    payload = {
        "repository": scenario.repository,
        "label": "benchmark fixture — not production commit history",
        "scenario_id": scenario.id,
        "commits": commits,
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


def ensure_fixtures() -> None:
    """Regenerate all scenario fixtures on disk."""

    for scenario in list_scenarios():
        generate_openmetrics(scenario)
        generate_commit_fixture(scenario)
