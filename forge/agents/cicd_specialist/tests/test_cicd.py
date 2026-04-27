from __future__ import annotations

from pathlib import Path

import pytest

from forge.agents.cicd_specialist.pipeline_generators import generate_pipeline
from forge.agents.librarian.ast_analyzer import ASTAnalyzer


@pytest.mark.unit
def test_generate_pipeline_for_fastapi_project(
    python_fastapi_project: Path,
) -> None:
    scan_result = ASTAnalyzer().analyze_project(python_fastapi_project)

    bundle = generate_pipeline(scan_result)

    assert "actions/setup-python@v5" in bundle.pipeline
    assert "ruff check ." in bundle.pipeline
    assert "mypy ." in bundle.pipeline
    assert "pytest -q" in bundle.pipeline
    assert (
        "docker build -t ghcr.io/devops-forge/python-fastapi:${{ github.sha }} ."
        in bundle.pipeline
    )
    assert bundle.confidence >= 0.85


@pytest.mark.unit
def test_generate_pipeline_for_go_project(
    go_service_project: Path,
) -> None:
    scan_result = ASTAnalyzer().analyze_project(go_service_project)

    bundle = generate_pipeline(scan_result)

    assert "actions/setup-go@v5" in bundle.pipeline
    assert "go mod download" in bundle.pipeline
    assert "go test ./..." in bundle.pipeline
