from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from forge.agents.librarian.ast_analyzer import CodebaseScanResult


class ServerlessBundle(BaseModel):
    """Generated assets for a serverless deployment strategy."""

    files: dict[str, str] = Field(
        default_factory=dict,
        description="Serverless artifact content keyed by relative output path.",
    )
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


def generate_serverless_assets(
    scan_result: CodebaseScanResult,
    *,
    cloud: str,
) -> ServerlessBundle:
    """Generate AWS Lambda or Google Cloud Run assets for a scanned project."""

    app_name = _app_name(scan_result.project_path)
    port = scan_result.port or 8000
    if cloud == "aws":
        template = {
            "service": app_name,
            "provider": {"name": "aws", "runtime": _aws_runtime(scan_result.language)},
            "functions": {
                app_name: {
                    "handler": _aws_handler(scan_result),
                    "events": [{"httpApi": {"path": "/", "method": "get"}}],
                    "environment": {
                        env_var: f"set-{env_var.lower()}"
                        for env_var in scan_result.env_vars
                    },
                }
            },
        }
        return ServerlessBundle(
            files={"serverless.yml": yaml.safe_dump(template, sort_keys=False)},
            evidence=[
                "Selected AWS Lambda because the chosen cloud provider is AWS.",
                f"Mapped application entry point {scan_result.entry_point}.",
            ],
            confidence=0.82,
        )
    service_yaml = {
        "apiVersion": "serving.knative.dev/v1",
        "kind": "Service",
        "metadata": {"name": app_name},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "image": f"gcr.io/project/{app_name}:latest",
                            "ports": [{"containerPort": port}],
                            "env": [
                                {"name": env_var, "value": f"set-{env_var.lower()}"}
                                for env_var in scan_result.env_vars
                            ],
                        }
                    ]
                }
            }
        },
    }
    return ServerlessBundle(
        files={"cloudrun-service.yaml": yaml.safe_dump(service_yaml, sort_keys=False)},
        evidence=[
            "Selected Google Cloud Run because the chosen cloud provider is GCP.",
            f"Configured service port {port}.",
        ],
        confidence=0.82,
    )


def _app_name(project_path: str) -> str:
    return Path(project_path).name.strip().lower().replace("_", "-") or "app"


def _aws_runtime(language: str) -> str:
    if language == "node":
        return "nodejs20.x"
    return "python3.11"


def _aws_handler(scan_result: CodebaseScanResult) -> str:
    entry = scan_result.entry_point or "main.py"
    if entry.endswith(".js"):
        return entry.removesuffix(".js").replace("/", ".") + ".handler"
    return entry.removesuffix(".py").replace("/", ".") + ".handler"
