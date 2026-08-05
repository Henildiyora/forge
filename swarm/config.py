"""Environment-backed settings for the swarm."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration. Every field has a working default."""

    prometheus_url: str = Field(
        default="http://localhost:9090",
        description="Base URL of the Prometheus HTTP API.",
    )
    prometheus_timeout_seconds: float = Field(
        default=10.0, gt=0, description="Per-request timeout for Prometheus queries."
    )

    github_token: SecretStr | None = Field(
        default=None,
        description="Token for the live GitHub commit source. Unset means fixture replay.",
    )

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        description="Anthropic key. Unset means the Ops Agent uses its deterministic planner.",
    )
    anthropic_model: str = Field(
        default="claude-sonnet-4-20250514", description="Model used by the Ops Agent."
    )
    anthropic_max_tokens: int = Field(
        default=2048, ge=256, description="Response cap for Ops Agent calls."
    )
    anthropic_timeout_seconds: float = Field(
        default=60.0, gt=0, description="Per-request timeout for Anthropic calls."
    )

    sandbox_service_path: Path = Field(
        default=REPO_ROOT / "sandbox" / "target_service",
        description="Source tree copied into the dry-run sandbox.",
    )
    sandbox_timeout_seconds: float = Field(
        default=180.0, gt=0, description="Hard cap on a single dry-run attempt."
    )
    sandbox_startup_timeout_seconds: float = Field(
        default=30.0, gt=0, description="How long to wait for the container to serve traffic."
    )
    docker_binary: str = Field(default="docker", description="Docker CLI to shell out to.")

    runs_dir: Path = Field(
        default=REPO_ROOT / ".swarm" / "runs",
        description="Where per-run event streams are written for the dashboard.",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def has_anthropic(self) -> bool:
        """Whether a real LLM call is possible."""

        return self.anthropic_api_key is not None and bool(
            self.anthropic_api_key.get_secret_value()
        )

    @property
    def has_github(self) -> bool:
        """Whether the live GitHub commit source is usable."""

        return self.github_token is not None and bool(self.github_token.get_secret_value())


def get_settings() -> Settings:
    """Build settings from the environment and ``.env``."""

    return Settings()
