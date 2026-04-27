from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from swarm.agents.librarian.ast_analyzer import CodebaseScanResult


class KubernetesManifestBundle(BaseModel):
    """Generated Kubernetes manifests for a scanned codebase."""

    manifests: dict[str, str] = Field(
        default_factory=dict,
        description="Manifest content keyed by filename.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting generation choices.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score for the manifest set.",
    )


def generate_manifests(
    scan_result: CodebaseScanResult,
    *,
    namespace: str = "default",
) -> KubernetesManifestBundle:
    """Generate deployment-oriented Kubernetes manifests from scan output."""

    app_name = _application_name(scan_result.project_path)
    port = scan_result.port or _default_port(scan_result.language)
    image = f"ghcr.io/devops-swarm/{app_name}:latest"

    manifests: dict[str, str] = {
        "deployment.yaml": _dump_yaml(
            _deployment_manifest(
                app_name=app_name,
                namespace=namespace,
                image=image,
                port=port,
                framework=scan_result.framework,
                env_vars=scan_result.env_vars,
            )
        ),
        "service.yaml": _dump_yaml(
            _service_manifest(
                app_name=app_name,
                namespace=namespace,
                port=port,
            )
        ),
    }
    if scan_result.env_vars:
        manifests["configmap.yaml"] = _dump_yaml(
            _config_map_manifest(
                app_name=app_name,
                namespace=namespace,
                env_vars=scan_result.env_vars,
                databases=scan_result.database_connections,
            )
        )

    evidence = [
        f"Generated Deployment and Service for port {port}.",
        f"Configured image repository {image}.",
    ]
    if scan_result.env_vars:
        evidence.append(
            "Mapped runtime configuration through ConfigMap keys: "
            f"{', '.join(scan_result.env_vars)}."
        )
    if scan_result.framework != "unknown":
        evidence.append(f"Added health probes for detected framework {scan_result.framework}.")
    return KubernetesManifestBundle(
        manifests=manifests,
        evidence=evidence,
        confidence=_confidence_for(scan_result),
    )


def _application_name(project_path: str) -> str:
    project_name = Path(project_path).name.strip().lower() or "app"
    sanitized = "".join(character if character.isalnum() else "-" for character in project_name)
    return sanitized.strip("-") or "app"


def _default_port(language: str) -> int:
    if language == "node":
        return 3000
    if language == "go":
        return 8080
    return 8000


def _deployment_manifest(
    *,
    app_name: str,
    namespace: str,
    image: str,
    port: int,
    framework: str,
    env_vars: list[str],
) -> dict[str, object]:
    container: dict[str, object] = {
        "name": app_name,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "ports": [{"containerPort": port}],
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
        "livenessProbe": _http_probe(path=_health_path(framework), port=port),
        "readinessProbe": _http_probe(path=_health_path(framework), port=port),
    }
    if env_vars:
        container["envFrom"] = [{"configMapRef": {"name": f"{app_name}-config"}}]

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": app_name, "namespace": namespace},
        "spec": {
            "replicas": 2,
            "selector": {"matchLabels": {"app": app_name}},
            "template": {
                "metadata": {"labels": {"app": app_name}},
                "spec": {"containers": [container]},
            },
        },
    }


def _service_manifest(
    *,
    app_name: str,
    namespace: str,
    port: int,
) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": app_name, "namespace": namespace},
        "spec": {
            "selector": {"app": app_name},
            "ports": [
                {
                    "name": "http",
                    "port": port,
                    "targetPort": port,
                }
            ],
            "type": "ClusterIP",
        },
    }


def _config_map_manifest(
    *,
    app_name: str,
    namespace: str,
    env_vars: list[str],
    databases: list[str],
) -> dict[str, object]:
    data = {env_var: _env_value(env_var, databases) for env_var in env_vars}
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": f"{app_name}-config", "namespace": namespace},
        "data": data,
    }


def _http_probe(*, path: str, port: int) -> dict[str, object]:
    return {
        "httpGet": {"path": path, "port": port},
        "initialDelaySeconds": 10,
        "periodSeconds": 15,
    }


def _health_path(framework: str) -> str:
    return "/health" if framework != "django" else "/health/"


def _env_value(env_var: str, databases: list[str]) -> str:
    database_defaults = {
        "DATABASE_URL": "postgresql://postgres:postgres@postgres:5432/app",
        "MONGO_URL": "mongodb://mongo:27017/app",
        "REDIS_URL": "redis://redis:6379/0",
        "MYSQL_URL": "mysql://root:password@mysql:3306/app",
    }
    if env_var in database_defaults:
        return database_defaults[env_var]
    if env_var == "SECRET_KEY":
        return "change-me"
    if "postgres" in databases and env_var.endswith("_URL"):
        return database_defaults["DATABASE_URL"]
    return f"set-{env_var.lower()}"


def _dump_yaml(document: dict[str, object]) -> str:
    return cast(str, yaml.safe_dump(document, sort_keys=False))


def _confidence_for(scan_result: CodebaseScanResult) -> float:
    score = 0.62
    if scan_result.port is not None:
        score += 0.12
    if scan_result.framework != "unknown":
        score += 0.08
    if scan_result.entry_point:
        score += 0.08
    if scan_result.env_vars:
        score += 0.05
    if scan_result.database_connections:
        score += 0.03
    return min(score, 0.98)
