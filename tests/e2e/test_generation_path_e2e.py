"""E2E test of the generation pipeline that does not require Docker.

Walks every fixture project through:
    scan -> heuristic intent -> deterministic strategy selection -> artifact
    generation -> on-disk write -> well-formed-output assertions.

These tests must always run in CI (they have no external dependencies) and
prove that the conversation + generator chain is internally consistent.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
import yaml

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


def _run_generation(
    project_path: Path,
    goal: str,
    tmp_path: Path,
) -> tuple[Path, list[str], DeploymentStrategy]:
    workdir = tmp_path / project_path.name
    shutil.copytree(project_path, workdir)
    shutil.rmtree(workdir / ".forge", ignore_errors=True)

    settings = Settings(app_env="test", llm_backend="heuristic")
    bus = local_message_bus(settings)
    workspace = ForgeWorkspace(workdir, settings)
    librarian = LibrarianAgent(settings=settings, message_bus=bus)
    scan = asyncio.run(librarian.analyze_codebase(str(workdir)))
    workspace.save_index(scan)

    llm = LLMClient(settings)
    engine = ConversationEngine(llm=llm, scan_result=scan)
    intent = asyncio.run(engine.interpret_intent(goal))
    selection = select_strategy(scan_result=scan, intent=intent)

    generated = asyncio.run(
        generate_strategy_artifacts(
            settings=settings,
            project_path=workdir,
            strategy=selection.strategy,
            cloud=intent.mentioned_cloud,
            message_bus=bus,
        )
    )
    artifact_dir = workdir / ".forge" / "generated"
    written = write_generated_artifacts(
        output_dir=artifact_dir,
        generated=generated,
        workspace=workspace,
    )
    return artifact_dir, written, selection.strategy


def test_python_fastapi_generates_well_formed_docker_compose(
    tmp_path: Path,
    python_fastapi_project: Path,
) -> None:
    artifact_dir, written, strategy = _run_generation(
        python_fastapi_project,
        goal="just a simple API I want to test locally with docker",
        tmp_path=tmp_path,
    )
    assert strategy == DeploymentStrategy.DOCKER_COMPOSE
    assert "Dockerfile" in written
    assert "docker-compose.generated.yml" in written

    dockerfile = (artifact_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python" in dockerfile
    assert "8000" in dockerfile

    compose_yaml = (artifact_dir / "docker-compose.generated.yml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(compose_yaml)
    assert isinstance(parsed, dict)
    assert "services" in parsed
    assert any(
        "8000" in str(spec.get("ports", []))
        for spec in parsed["services"].values()
    )


def test_brownfield_fastapi_extends_existing_infra(
    tmp_path: Path,
    brownfield_fastapi_project: Path,
) -> None:
    artifact_dir, written, strategy = _run_generation(
        brownfield_fastapi_project,
        goal="I already have Kubernetes manifests, just give me what's missing",
        tmp_path=tmp_path,
    )
    assert strategy == DeploymentStrategy.EXTEND_EXISTING
    assert artifact_dir.exists()
    assert len(written) >= 1


def test_node_express_generation_produces_artifacts(
    tmp_path: Path,
    node_express_project: Path,
) -> None:
    artifact_dir, written, strategy = _run_generation(
        node_express_project,
        goal="put this in a docker container so I can test it",
        tmp_path=tmp_path,
    )
    assert strategy in {
        DeploymentStrategy.DOCKER_COMPOSE,
        DeploymentStrategy.KUBERNETES,
    }
    assert artifact_dir.exists()
    assert "Dockerfile" in written


def test_serverless_aws_generates_lambda_and_api_gateway(
    tmp_path: Path,
    serverless_aws_project: Path,
) -> None:
    artifact_dir, written, strategy = _run_generation(
        serverless_aws_project,
        goal="deploy this as AWS Lambda functions please",
        tmp_path=tmp_path,
    )
    assert strategy == DeploymentStrategy.SERVERLESS
    assert artifact_dir.exists()
    assert any("lambda" in name.lower() or "serverless" in name.lower() for name in written), (
        f"Expected at least one Lambda/serverless asset in {written}"
    )
