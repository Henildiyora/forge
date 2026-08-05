"""Backfill seeded OpenMetrics into a real Prometheus TSDB via promtool.

Usage:
    python -m benchmark.seed_prometheus --scenario payment_timeout
    python -m benchmark.seed_prometheus --all

Stops Prometheus, writes blocks into the compose volume with a one-shot
``promtool`` container, then starts Prometheus again. Data is a labelled
benchmark fixture; the Monitoring Agent then queries it over the real
``/api/v1/query_range`` HTTP API.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from benchmark.scenarios import SCENARIOS, ensure_fixtures, load_scenario
from swarm.config import REPO_ROOT

SEED_DIR = REPO_ROOT / "infrastructure" / "prometheus" / "seed"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
VOLUME = "devops-swarm_prometheus-data"
PROMETHEUS_URL = "http://localhost:9090"
IMAGE = "prom/prometheus:v2.53.1"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=False, text=True, capture_output=True, **kwargs)


def wait_ready(url: str = PROMETHEUS_URL, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(f"{url}/-/ready", timeout=2.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"Prometheus not ready at {url} within {timeout}s")


def seed_scenario(scenario_id: str) -> None:
    scenario = load_scenario(scenario_id)
    ensure_fixtures()
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    dest = SEED_DIR / f"{scenario.id}.openmetrics"
    shutil.copyfile(scenario.metrics_fixture, dest)
    print(f"Copied fixture → {dest}")

    # Ensure Compose owns the named volume (do not docker-volume-create outside
    # Compose — that triggers the "already exists but was not created by
    # Docker Compose" warning on later `up`).
    ensure_vol = _run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "up",
            "-d",
            "--no-start",
            "prometheus",
        ],
        cwd=str(REPO_ROOT),
    )
    if ensure_vol.returncode != 0:
        # Older compose may lack --no-start; fall back to create.
        ensure_vol = _run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "create",
                "prometheus",
            ],
            cwd=str(REPO_ROOT),
        )
        if ensure_vol.returncode != 0:
            raise RuntimeError(ensure_vol.stderr or ensure_vol.stdout)

    # Stop Prometheus so we can write blocks safely into its data volume.
    _run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "stop", "prometheus"],
        cwd=str(REPO_ROOT),
    )

    promtool = _run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "65534:65534",
            "--entrypoint",
            "promtool",
            "-v",
            f"{SEED_DIR}:/seed:ro",
            "-v",
            f"{VOLUME}:/prometheus",
            IMAGE,
            "tsdb",
            "create-blocks-from",
            "openmetrics",
            f"/seed/{scenario.id}.openmetrics",
            "/prometheus",
        ]
    )
    print(promtool.stdout)
    if promtool.returncode != 0:
        print(promtool.stderr, file=sys.stderr)
        raise RuntimeError(f"promtool failed for {scenario_id}")

    # Keep only a reasonable number of block dirs; print count for debugging.
    listing = _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            "-v",
            f"{VOLUME}:/prometheus",
            IMAGE,
            "-c",
            "ls -1 /prometheus | wc -l",
        ]
    )
    print("volume entries:", listing.stdout.strip())

    up = _run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "prometheus"],
        cwd=str(REPO_ROOT),
    )
    if up.returncode != 0:
        raise RuntimeError(up.stderr or up.stdout)
    wait_ready()
    print(f"Seeded scenario {scenario_id!r} into Prometheus at {PROMETHEUS_URL}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", help="Scenario id to seed")
    parser.add_argument("--all", action="store_true", help="Seed every scenario")
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Only regenerate fixture files; do not touch Prometheus",
    )
    args = parser.parse_args(argv)

    ensure_fixtures()
    print(f"Fixtures ready under {REPO_ROOT / 'benchmark' / 'fixtures'}")
    if args.generate_only:
        return 0

    if shutil.which("docker") is None:
        raise SystemExit("docker not on PATH")

    ids = list(SCENARIOS) if args.all else [args.scenario]
    if not ids or ids == [None]:
        parser.error("pass --scenario ID or --all")
    for scenario_id in ids:
        assert scenario_id is not None
        seed_scenario(scenario_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
