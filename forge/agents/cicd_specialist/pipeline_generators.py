from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from forge.agents.librarian.ast_analyzer import CodebaseScanResult


class PipelineBundle(BaseModel):
    """Generated CI/CD pipeline for a scanned repository."""

    pipeline: str = Field(description="Rendered pipeline content.")
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting pipeline choices.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score for the pipeline.",
    )


def generate_pipeline(scan_result: CodebaseScanResult) -> PipelineBundle:
    """Generate a GitHub Actions pipeline from Librarian scan output."""

    app_name = _application_name(scan_result.project_path)
    language = scan_result.language
    pipeline_document = {
        "name": f"{app_name}-ci",
        "on": {
            "push": {"branches": ["main"]},
            "pull_request": {"branches": ["main"]},
        },
        "jobs": {
            "test": _test_job(scan_result),
            "build": _build_job(scan_result),
        },
    }
    evidence = [
        f"Generated GitHub Actions workflow for detected language {language}.",
        "Added a test stage before the Docker build stage.",
    ]
    if scan_result.framework != "unknown":
        evidence.append(f"Included framework-aware test setup for {scan_result.framework}.")
    return PipelineBundle(
        pipeline=cast(str, yaml.safe_dump(pipeline_document, sort_keys=False)),
        evidence=evidence,
        confidence=_confidence_for(scan_result),
    )


def _application_name(project_path: str) -> str:
    project_name = Path(project_path).name.strip().lower() or "app"
    sanitized = "".join(character if character.isalnum() else "-" for character in project_name)
    return sanitized.strip("-") or "app"


def _test_job(scan_result: CodebaseScanResult) -> dict[str, object]:
    job: dict[str, object] = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {"uses": "actions/checkout@v4"},
            *_setup_steps(scan_result),
            *_install_steps(scan_result),
            *_lint_and_test_steps(scan_result),
        ],
    }
    return job


def _build_job(scan_result: CodebaseScanResult) -> dict[str, object]:
    app_name = _application_name(scan_result.project_path)
    return {
        "runs-on": "ubuntu-latest",
        "needs": ["test"],
        "steps": [
            {"uses": "actions/checkout@v4"},
            {"uses": "docker/setup-buildx-action@v3"},
            {
                "name": "Build container image",
                "run": f"docker build -t ghcr.io/devops-forge/{app_name}:${{{{ github.sha }}}} .",
            },
        ],
    }


def _setup_steps(scan_result: CodebaseScanResult) -> list[dict[str, object]]:
    if scan_result.language == "python":
        return [
            {
                "uses": "actions/setup-python@v5",
                "with": {"python-version": "3.11"},
            }
        ]
    if scan_result.language == "node":
        return [
            {
                "uses": "actions/setup-node@v4",
                "with": {"node-version": "20"},
            }
        ]
    if scan_result.language == "go":
        return [
            {
                "uses": "actions/setup-go@v5",
                "with": {"go-version": "1.22"},
            }
        ]
    return []


def _install_steps(scan_result: CodebaseScanResult) -> list[dict[str, object]]:
    if scan_result.language == "python":
        return [
            {
                "name": "Install dependencies",
                "run": (
                    "python -m pip install --upgrade pip && "
                    "(python -m pip install '.[dev]' || "
                    "python -m pip install pytest ruff mypy fastapi uvicorn)"
                ),
            }
        ]
    if scan_result.language == "node":
        return [{"name": "Install dependencies", "run": "npm ci || npm install"}]
    if scan_result.language == "go":
        return [{"name": "Download dependencies", "run": "go mod download"}]
    return []


def _lint_and_test_steps(scan_result: CodebaseScanResult) -> list[dict[str, object]]:
    if scan_result.language == "python":
        return [
            {"name": "Lint", "run": "ruff check ."},
            {"name": "Type-check", "run": "mypy ."},
            {"name": "Run tests", "run": "pytest -q"},
        ]
    if scan_result.language == "node":
        return [
            {"name": "Run tests", "run": "npm test --if-present"},
        ]
    if scan_result.language == "go":
        return [
            {"name": "Run tests", "run": "go test ./..."},
        ]
    return [{"name": "Validate repository", "run": "echo 'No language-specific checks configured'"}]


def _confidence_for(scan_result: CodebaseScanResult) -> float:
    score = 0.6
    if scan_result.language != "unknown":
        score += 0.15
    if scan_result.framework != "unknown":
        score += 0.05
    if scan_result.entry_point:
        score += 0.05
    if scan_result.port is not None:
        score += 0.05
    if scan_result.file_count > 0:
        score += 0.05
    return min(score, 0.95)
