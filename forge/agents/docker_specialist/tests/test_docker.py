from __future__ import annotations

from pathlib import Path

import pytest

from forge.agents.docker_specialist.generators import generate_docker_assets
from forge.agents.librarian.ast_analyzer import ASTAnalyzer


@pytest.mark.unit
def test_generate_docker_assets_for_fastapi_project(
    python_fastapi_project: Path,
) -> None:
    scan_result = ASTAnalyzer().analyze_project(python_fastapi_project)

    bundle = generate_docker_assets(scan_result)

    assert "FROM python:3.11-slim" in bundle.dockerfile
    assert 'CMD ["uvicorn", "main:app"' in bundle.dockerfile
    assert "EXPOSE 8000" in bundle.dockerfile
    assert "DATABASE_URL" in bundle.docker_compose
    assert "SECRET_KEY" in bundle.docker_compose
    assert "postgres:" in bundle.docker_compose
    assert bundle.confidence >= 0.9


@pytest.mark.unit
def test_generate_docker_assets_for_express_project(
    node_express_project: Path,
) -> None:
    scan_result = ASTAnalyzer().analyze_project(node_express_project)

    bundle = generate_docker_assets(scan_result)

    assert "FROM node:20-alpine" in bundle.dockerfile
    assert 'CMD ["node", "index.js"]' in bundle.dockerfile
    assert "EXPOSE 3000" in bundle.dockerfile
    assert "PORT: '3000'" in bundle.docker_compose or 'PORT: "3000"' in bundle.docker_compose
    assert "MONGO_URL" in bundle.docker_compose
    assert "mongo:" in bundle.docker_compose
