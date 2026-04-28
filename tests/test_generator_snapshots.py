"""Golden-file snapshot tests for the Docker, K8s, and CI/CD generators.

These tests pin canonical generator output for representative
``CodebaseScanResult`` inputs. When you intentionally change a generator,
re-record the snapshot by running:

    UPDATE_SNAPSHOTS=1 pytest tests/test_generator_snapshots.py -q

A drift in any generator that is NOT part of an intentional change will fail
loudly here, which is exactly what we want for trust.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from forge.agents.cicd_specialist.pipeline_generators import generate_pipeline
from forge.agents.docker_specialist.generators import generate_docker_assets
from forge.agents.k8s_specialist.manifest_generator import generate_manifests
from forge.agents.librarian.ast_analyzer import CodebaseScanResult

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)


def _sample_python_fastapi_scan() -> CodebaseScanResult:
    return CodebaseScanResult(
        project_path="/projects/sample-fastapi",
        language="python",
        framework="fastapi",
        entry_point="main.py",
        port=8000,
        env_vars=["DATABASE_URL", "SECRET_KEY"],
        database_connections=["postgres"],
        file_count=42,
        service_count=1,
        has_existing_infra=False,
        recent_changes=[],
    )


def _sample_node_express_scan() -> CodebaseScanResult:
    return CodebaseScanResult(
        project_path="/projects/sample-node",
        language="node",
        framework="express",
        entry_point="index.js",
        port=3000,
        env_vars=["DATABASE_URL"],
        database_connections=[],
        file_count=18,
        service_count=1,
        has_existing_infra=False,
        recent_changes=[],
    )


def _assert_or_record_snapshot(name: str, content: str) -> None:
    snapshot_path = SNAPSHOT_DIR / name
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        snapshot_path.write_text(content, encoding="utf-8")
        return
    if not snapshot_path.exists():
        snapshot_path.write_text(content, encoding="utf-8")
        return
    expected = snapshot_path.read_text(encoding="utf-8")
    assert expected == content, (
        f"Snapshot drift for {name}.\n"
        "Re-run with UPDATE_SNAPSHOTS=1 if this drift is intentional.\n"
        f"--- expected ---\n{expected}\n--- actual ---\n{content}"
    )


def test_python_fastapi_dockerfile_snapshot() -> None:
    bundle = generate_docker_assets(_sample_python_fastapi_scan())
    _assert_or_record_snapshot("python_fastapi.Dockerfile", bundle.dockerfile)


def test_python_fastapi_compose_snapshot() -> None:
    bundle = generate_docker_assets(_sample_python_fastapi_scan())
    parsed = yaml.safe_load(bundle.docker_compose)
    assert isinstance(parsed, dict)
    assert "services" in parsed
    _assert_or_record_snapshot("python_fastapi.docker-compose.yml", bundle.docker_compose)


def test_python_fastapi_k8s_manifests_snapshot() -> None:
    bundle = generate_manifests(_sample_python_fastapi_scan())
    assert "deployment.yaml" in bundle.manifests
    assert "service.yaml" in bundle.manifests
    for name in ("deployment.yaml", "service.yaml"):
        parsed = yaml.safe_load(bundle.manifests[name])
        assert isinstance(parsed, dict)
        assert "kind" in parsed
        _assert_or_record_snapshot(f"python_fastapi.{name}", bundle.manifests[name])


def test_python_fastapi_pipeline_snapshot() -> None:
    bundle = generate_pipeline(_sample_python_fastapi_scan())
    parsed = yaml.safe_load(bundle.pipeline)
    assert isinstance(parsed, dict)
    assert "jobs" in parsed
    _assert_or_record_snapshot("python_fastapi.github-actions.yml", bundle.pipeline)


def test_node_express_dockerfile_snapshot() -> None:
    bundle = generate_docker_assets(_sample_node_express_scan())
    _assert_or_record_snapshot("node_express.Dockerfile", bundle.dockerfile)


def test_node_express_pipeline_snapshot() -> None:
    bundle = generate_pipeline(_sample_node_express_scan())
    _assert_or_record_snapshot("node_express.github-actions.yml", bundle.pipeline)


def test_generated_dockerfile_passes_basic_lint() -> None:
    bundle = generate_docker_assets(_sample_python_fastapi_scan())
    assert "FROM " in bundle.dockerfile
    assert bundle.dockerfile.count("FROM ") >= 1
    assert "EXPOSE 8000" in bundle.dockerfile
    assert "CMD" in bundle.dockerfile or "ENTRYPOINT" in bundle.dockerfile


@pytest.mark.parametrize("manifest_name", ["deployment.yaml", "service.yaml"])
def test_generated_k8s_manifests_have_required_fields(manifest_name: str) -> None:
    bundle = generate_manifests(_sample_python_fastapi_scan())
    parsed = yaml.safe_load(bundle.manifests[manifest_name])
    assert parsed["apiVersion"]
    assert parsed["kind"]
    assert parsed["metadata"]["name"]
