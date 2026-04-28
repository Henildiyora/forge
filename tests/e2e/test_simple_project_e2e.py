"""End-to-end test for the docker-compose strategy path.

Walks a real ``python_fastapi`` fixture through:
    scan -> strategy selection -> generate -> docker build -> run -> /health 200.

The test is gated by Docker availability. On a CI host without Docker it
skips cleanly; on a developer laptop with Docker it executes the real loop.

Run only this suite explicitly with:
    pytest -m e2e tests/e2e/test_simple_project_e2e.py -q
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from forge.agents.librarian.agent import LibrarianAgent
from forge.cli.runtime import local_message_bus
from forge.conversation.engine import ConversationEngine
from forge.conversation.strategy_selector import select_strategy
from forge.core.builds import (
    generate_strategy_artifacts,
    write_generated_artifacts,
)
from forge.core.config import Settings
from forge.core.llm import LLMClient
from forge.core.strategies import DeploymentStrategy
from forge.core.workspace import ForgeWorkspace

pytestmark = pytest.mark.e2e


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _wait_for_http_ok(url: str, timeout_seconds: float = 30.0) -> bool:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310
                if response.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_error = exc
        time.sleep(1.0)
    if last_error is not None:
        print(f"Waited for {url}; last error was {last_error!r}")
    return False


def _docker_run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_docker_compose_strategy_produces_a_running_container(
    tmp_path: Path,
    python_fastapi_project: Path,
    free_tcp_port: int,
) -> None:
    if not _docker_available():
        pytest.skip("Docker daemon is not available on this host")

    workdir = tmp_path / "project"
    shutil.copytree(python_fastapi_project, workdir)
    shutil.rmtree(workdir / ".forge", ignore_errors=True)

    settings = Settings(app_env="test", llm_backend="heuristic")
    bus = local_message_bus(settings)
    workspace = ForgeWorkspace(workdir, settings)
    librarian = LibrarianAgent(settings=settings, message_bus=bus)

    scan = asyncio.run(librarian.analyze_codebase(str(workdir)))

    llm = LLMClient(settings)
    engine = ConversationEngine(llm=llm, scan_result=scan)
    intent = asyncio.run(
        engine.interpret_intent("just a simple API I want to test locally with docker")
    )
    selection = select_strategy(scan_result=scan, intent=intent)
    assert selection.strategy == DeploymentStrategy.DOCKER_COMPOSE, (
        "Heuristic selector should route a single Python FastAPI service to docker-compose. "
        f"Got {selection.strategy} instead."
    )

    generated = asyncio.run(
        generate_strategy_artifacts(
            settings=settings,
            project_path=workdir,
            strategy=selection.strategy,
            cloud=None,
            message_bus=bus,
        )
    )
    assert generated.dockerfile is not None
    assert generated.docker_compose is not None

    artifact_dir = workdir / ".forge" / "generated"
    written = write_generated_artifacts(
        output_dir=artifact_dir,
        generated=generated,
        workspace=workspace,
    )
    assert "Dockerfile" in written
    assert "docker-compose.generated.yml" in written

    image_tag = f"forge-e2e-{free_tcp_port}:test"
    container_name = f"forge-e2e-{free_tcp_port}"

    build_proc = subprocess.run(
        ["docker", "build", "-f", str(artifact_dir / "Dockerfile"), "-t", image_tag, str(workdir)],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert build_proc.returncode == 0, (
        f"docker build failed:\n--- stdout ---\n{build_proc.stdout}\n"
        f"--- stderr ---\n{build_proc.stderr}"
    )

    try:
        run_proc = _docker_run(
            [
                "run",
                "-d",
                "--rm",
                "--name",
                container_name,
                "-p",
                f"{free_tcp_port}:8000",
                image_tag,
            ]
        )
        assert run_proc.returncode == 0, (
            f"docker run failed:\n{run_proc.stdout}\n{run_proc.stderr}"
        )

        ok = _wait_for_http_ok(f"http://127.0.0.1:{free_tcp_port}/health", timeout_seconds=30.0)
        if not ok:
            logs = _docker_run(["logs", container_name])
            pytest.fail(
                "Container never returned 200 on /health.\n"
                f"stdout:\n{logs.stdout}\nstderr:\n{logs.stderr}"
            )
    finally:
        _docker_run(["rm", "-f", container_name])
        _docker_run(["rmi", "-f", image_tag])
