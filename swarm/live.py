"""Live service configuration: config.yaml + env secrets, no fixture coupling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from swarm.config import REPO_ROOT, Settings, get_settings
from swarm.schemas import SwarmConfig

CONFIG_YAML_PATH = REPO_ROOT / "config.yaml"
DEFAULT_ALLOWLIST = ["env_override", "file_replace"]


class LiveConfigError(ValueError):
    """User-facing configuration error (no stack traces in the CLI)."""


class LiveConfig(BaseModel):
    """User-supplied wiring for ``swarm run --live``.

    Secrets (GitHub / Anthropic tokens) are never stored here — they come from
    ``.env`` / the process environment via :class:`Settings`.
    """

    prometheus_url: str = Field(
        ...,
        min_length=1,
        description="Base URL of the Prometheus HTTP API, e.g. http://localhost:9090",
    )
    error_metric_query: str = Field(
        ...,
        min_length=1,
        description="Raw PromQL for the error-rate (or similar) time series.",
    )
    github_repo: str = Field(
        ...,
        min_length=3,
        description="Repository in owner/name form.",
    )
    service_name: str = Field(
        default="service",
        min_length=1,
        description="Logical service label used in run metadata.",
    )
    service_paths: list[str] = Field(
        default_factory=lambda: ["./"],
        description="Repo path prefixes used for commit blame scoring.",
    )
    service_health_endpoints: list[str] = Field(
        ...,
        min_length=1,
        description="HTTP paths the dry-run must get 2xx from (GET), e.g. ['/health'].",
    )
    service_dockerfile_path: str = Field(
        ...,
        min_length=1,
        description="Path to the Dockerfile to build for dry-run validation.",
    )
    service_build_context: str = Field(
        ...,
        min_length=1,
        description="Docker build context directory.",
    )
    fix_action_allowlist: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWLIST),
        description="Allowed ProposedFix action kinds for this service.",
    )
    lookback_minutes: int = Field(default=60, ge=5)
    container_port: int = Field(
        default=8080,
        ge=1,
        le=65535,
        description="Port the container listens on inside the network namespace.",
    )
    runtime_env: dict[str, str] = Field(
        default_factory=dict,
        description="Env vars to inject when starting the dry-run container.",
    )

    @field_validator("prometheus_url")
    @classmethod
    def _strip_prometheus_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("prometheus_url must start with http:// or https://")
        return cleaned

    @field_validator("github_repo")
    @classmethod
    def _validate_repo(cls, value: str) -> str:
        cleaned = value.strip()
        parts = cleaned.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError("github_repo must be in owner/name form")
        return cleaned

    @field_validator("service_health_endpoints")
    @classmethod
    def _normalize_endpoints(cls, value: list[str]) -> list[str]:
        endpoints: list[str] = []
        for raw in value:
            item = raw.strip()
            if not item:
                continue
            if not item.startswith("/"):
                item = "/" + item
            endpoints.append(item)
        if not endpoints:
            raise ValueError("service_health_endpoints must contain at least one path")
        return endpoints

    @field_validator("fix_action_allowlist")
    @classmethod
    def _normalize_allowlist(cls, value: list[str]) -> list[str]:
        allowed = {item.strip().lower() for item in value if item.strip()}
        if not allowed:
            raise ValueError("fix_action_allowlist cannot be empty")
        return sorted(allowed)

    def to_swarm_config(self, *, max_repair_attempts: int = 0) -> SwarmConfig:
        """Map live settings onto the shared graph config object."""

        dockerfile = Path(self.service_dockerfile_path).expanduser()
        context = Path(self.service_build_context).expanduser()
        if not context.is_absolute():
            context = (REPO_ROOT / context).resolve()
        else:
            context = context.resolve()
        if not dockerfile.is_absolute():
            dockerfile = (REPO_ROOT / dockerfile).resolve()
        else:
            dockerfile = dockerfile.resolve()

        return SwarmConfig(
            service=self.service_name,
            metric_query=self.error_metric_query,
            lookback_minutes=self.lookback_minutes,
            repository=self.github_repo,
            service_paths=list(self.service_paths),
            commit_source="live",
            runtime_env=dict(self.runtime_env),
            max_repair_attempts=max_repair_attempts,
            scenario_id=None,
            health_endpoints=list(self.service_health_endpoints),
            service_root=str(context),
            dockerfile_path=str(dockerfile),
            container_port=self.container_port,
            fix_action_allowlist=list(self.fix_action_allowlist),
        )

    def to_yaml_dict(self) -> dict[str, Any]:
        """Serialize for config.yaml — never includes secrets."""

        return self.model_dump(mode="json")


def save_live_config(config: LiveConfig, path: Path | None = None) -> Path:
    """Write non-secret live config to YAML."""

    target = path or CONFIG_YAML_PATH
    payload = {
        "#": "Generated by `swarm init`. Put GITHUB_TOKEN in .env — never here.",
        **config.to_yaml_dict(),
    }
    # PyYAML will quote the '#' key oddly; write a header manually instead.
    body = yaml.safe_dump(config.to_yaml_dict(), sort_keys=False, default_flow_style=False)
    text = (
        "# DevOps Swarm live config — generated by `swarm init`.\n"
        "# Secrets (GITHUB_TOKEN, ANTHROPIC_API_KEY) belong in .env only.\n"
        + body
    )
    target.write_text(text, encoding="utf-8")
    return target


def load_live_config(path: Path | None = None, settings: Settings | None = None) -> LiveConfig:
    """Load LiveConfig from YAML and validate required fields.

    Tokens are read from Settings/env and are not required to be in the YAML.
    Callers that need GitHub must still check ``settings.has_github``.
    """

    del settings  # reserved for future overlay fields; tokens stay on Settings
    target = path or CONFIG_YAML_PATH
    if not target.exists():
        raise LiveConfigError(
            f"No live config at {target}. Run `swarm init` first, or copy "
            f"config.example.yaml to config.yaml and edit it."
        )
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise LiveConfigError(f"Invalid YAML in {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LiveConfigError(f"{target} must contain a YAML mapping of fields.")
    try:
        return LiveConfig.model_validate(raw)
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()))
            parts.append(f"{loc}: {err.get('msg')}")
        raise LiveConfigError(
            "Invalid live config:\n  - " + "\n  - ".join(parts)
        ) from exc


def require_github_token(settings: Settings | None = None) -> str:
    """Return the GitHub token or raise a clear LiveConfigError."""

    cfg = settings or get_settings()
    if not cfg.has_github:
        raise LiveConfigError(
            "GITHUB_TOKEN is not set. Add it to .env (see the output of `swarm init`) "
            "and retry. Never put the token in config.yaml."
        )
    return cfg.github_token.get_secret_value()  # type: ignore[union-attr]
