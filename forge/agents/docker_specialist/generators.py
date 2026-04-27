from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from forge.agents.librarian.ast_analyzer import CodebaseScanResult

RuntimeLanguage = Literal["python", "node", "go", "unknown"]


class DockerAssetBundle(BaseModel):
    """Generated container artifacts for a scanned codebase."""

    dockerfile: str = Field(description="Generated Dockerfile content.")
    docker_compose: str = Field(description="Generated docker-compose content.")
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting generation choices.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score for the generated assets.",
    )


def generate_docker_assets(scan_result: CodebaseScanResult) -> DockerAssetBundle:
    """Generate Docker build assets from Librarian scan output."""

    runtime = _normalize_language(scan_result.language)
    port = scan_result.port or _default_port(runtime, scan_result.framework)
    app_name = _application_name(scan_result.project_path)
    dockerfile = _build_dockerfile(scan_result=scan_result, runtime=runtime, port=port)
    docker_compose = _build_docker_compose(
        scan_result=scan_result,
        app_name=app_name,
        port=port,
    )
    evidence = [
        f"Selected {runtime} container runtime for detected language {scan_result.language}.",
        f"Configured container port {port} from scan results.",
        f"Using entry point {scan_result.entry_point or 'repository default'} for startup.",
    ]
    if scan_result.database_connections:
        evidence.append(
            "Provisioned local compose dependencies for "
            f"{', '.join(scan_result.database_connections)}."
        )
    if scan_result.env_vars:
        evidence.append(
            f"Propagated runtime environment variables: {', '.join(scan_result.env_vars)}."
        )
    confidence = _confidence_for(scan_result=scan_result, runtime=runtime)
    return DockerAssetBundle(
        dockerfile=dockerfile,
        docker_compose=docker_compose,
        evidence=evidence,
        confidence=confidence,
    )


def _normalize_language(language: str) -> RuntimeLanguage:
    if language == "python":
        return "python"
    if language == "node":
        return "node"
    if language == "go":
        return "go"
    return "unknown"


def _default_port(runtime: RuntimeLanguage, framework: str) -> int:
    if runtime == "python" and framework == "fastapi":
        return 8000
    if runtime == "node":
        return 3000
    if runtime == "go":
        return 8080
    return 8000


def _application_name(project_path: str) -> str:
    project_name = Path(project_path).name.strip().lower() or "app"
    sanitized = "".join(character if character.isalnum() else "-" for character in project_name)
    return sanitized.strip("-") or "app"


def _build_dockerfile(
    *,
    scan_result: CodebaseScanResult,
    runtime: RuntimeLanguage,
    port: int,
) -> str:
    if runtime == "python":
        return _python_dockerfile(scan_result, port)
    if runtime == "node":
        return _node_dockerfile(scan_result, port)
    if runtime == "go":
        return _go_dockerfile(scan_result, port)
    return _fallback_dockerfile(scan_result.entry_point, port)


def _python_dockerfile(scan_result: CodebaseScanResult, port: int) -> str:
    entry_module = _python_module(scan_result.entry_point)
    install_packages = _python_runtime_packages(scan_result.framework)
    command = (
        f'CMD ["uvicorn", "{entry_module}:app", "--host", "0.0.0.0", "--port", "{port}"]'
        if scan_result.framework == "fastapi"
        else f'CMD ["python", "{scan_result.entry_point or "main.py"}"]'
    )
    lines = [
        "FROM python:3.11-slim",
        "",
        "ENV PYTHONDONTWRITEBYTECODE=1",
        "ENV PYTHONUNBUFFERED=1",
        f"ENV PORT={port}",
        "",
        "WORKDIR /app",
        "",
        "COPY . .",
        (
            "RUN pip install --no-cache-dir --upgrade pip && "
            "if [ -f requirements.txt ]; then "
            "pip install --no-cache-dir -r requirements.txt; "
            f"else pip install --no-cache-dir {install_packages}; fi"
        ),
        "",
        f"EXPOSE {port}",
        command,
    ]
    return "\n".join(lines)


def _node_dockerfile(scan_result: CodebaseScanResult, port: int) -> str:
    entry_point = scan_result.entry_point or "index.js"
    lines = [
        "FROM node:20-alpine",
        "",
        "WORKDIR /app",
        "",
        "COPY package*.json ./",
        "RUN npm ci --omit=dev || npm install --omit=dev",
        "COPY . .",
        "",
        f"EXPOSE {port}",
        f'CMD ["node", "{entry_point}"]',
    ]
    return "\n".join(lines)


def _go_dockerfile(scan_result: CodebaseScanResult, port: int) -> str:
    build_target = f"./{scan_result.entry_point}" if scan_result.entry_point else "./..."
    lines = [
        "FROM golang:1.22-alpine AS builder",
        "",
        "WORKDIR /src",
        "",
        "COPY go.mod go.sum* ./",
        "RUN go mod download || true",
        "COPY . .",
        f"RUN CGO_ENABLED=0 GOOS=linux go build -o /bin/app {build_target}",
        "",
        "FROM gcr.io/distroless/base-debian12",
        "",
        "WORKDIR /app",
        "COPY --from=builder /bin/app /app/app",
        f"EXPOSE {port}",
        'CMD ["/app/app"]',
    ]
    return "\n".join(lines)


def _fallback_dockerfile(entry_point: str, port: int) -> str:
    lines = [
        "FROM alpine:3.20",
        "",
        "WORKDIR /app",
        "COPY . .",
        f"EXPOSE {port}",
        f'CMD ["sh", "-c", "echo Unsupported project type for {entry_point or "app"}"]',
    ]
    return "\n".join(lines)


def _python_module(entry_point: str) -> str:
    path = entry_point.removesuffix(".py")
    return path.replace("/", ".") if path else "main"


def _python_runtime_packages(framework: str) -> str:
    package_map = {
        "fastapi": "fastapi uvicorn",
        "flask": "flask gunicorn",
        "django": "django gunicorn",
    }
    return package_map.get(framework, "uvicorn")


def _build_docker_compose(
    *,
    scan_result: CodebaseScanResult,
    app_name: str,
    port: int,
) -> str:
    app_service: dict[str, object] = {
        "build": {"context": ".", "dockerfile": "Dockerfile"},
        "ports": [f"{port}:{port}"],
        "environment": _compose_environment(scan_result, port),
    }
    depends_on = list(_support_service_names(scan_result.database_connections))
    if depends_on:
        app_service["depends_on"] = depends_on

    services: dict[str, object] = {"app": app_service}
    for service_name, service_definition in _support_services(scan_result).items():
        services[service_name] = service_definition

    compose_document = {
        "version": "3.9",
        "name": app_name,
        "services": services,
    }
    return cast(str, yaml.safe_dump(compose_document, sort_keys=False))


def _compose_environment(
    scan_result: CodebaseScanResult,
    port: int,
) -> dict[str, str]:
    environment: dict[str, str] = {}
    if "PORT" in scan_result.env_vars:
        environment["PORT"] = str(port)
    for env_var in scan_result.env_vars:
        if env_var == "PORT":
            continue
        environment[env_var] = _env_default(env_var, scan_result.database_connections)
    return environment


def _env_default(env_var: str, database_connections: list[str]) -> str:
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
    if "postgres" in database_connections and env_var.endswith("_URL"):
        return database_defaults["DATABASE_URL"]
    return f"set-{env_var.lower()}"


def _support_service_names(databases: list[str]) -> tuple[str, ...]:
    ordered_names: list[str] = []
    for database in databases:
        if database == "postgres":
            ordered_names.append("postgres")
        if database == "mongo":
            ordered_names.append("mongo")
        if database == "redis":
            ordered_names.append("redis")
        if database == "mysql":
            ordered_names.append("mysql")
    return tuple(ordered_names)


def _support_services(scan_result: CodebaseScanResult) -> dict[str, dict[str, object]]:
    services: dict[str, dict[str, object]] = {}
    for database in scan_result.database_connections:
        if database == "postgres":
            services["postgres"] = {
                "image": "postgres:16",
                "environment": {
                    "POSTGRES_DB": "app",
                    "POSTGRES_USER": "postgres",
                    "POSTGRES_PASSWORD": "postgres",
                },
                "ports": ["5432:5432"],
            }
        if database == "mongo":
            services["mongo"] = {
                "image": "mongo:7",
                "ports": ["27017:27017"],
            }
        if database == "redis":
            services["redis"] = {
                "image": "redis:7-alpine",
                "ports": ["6379:6379"],
            }
        if database == "mysql":
            services["mysql"] = {
                "image": "mysql:8",
                "environment": {
                    "MYSQL_DATABASE": "app",
                    "MYSQL_ROOT_PASSWORD": "password",
                },
                "ports": ["3306:3306"],
            }
    return services


def _confidence_for(
    *,
    scan_result: CodebaseScanResult,
    runtime: RuntimeLanguage,
) -> float:
    score = 0.55
    if runtime != "unknown":
        score += 0.15
    if scan_result.entry_point:
        score += 0.1
    if scan_result.port is not None:
        score += 0.1
    if scan_result.framework != "unknown":
        score += 0.05
    if scan_result.env_vars:
        score += 0.03
    if scan_result.database_connections:
        score += 0.02
    return min(score, 0.98)
