from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = Field(
        default="devops-swarm",
        description="Logical application name used in logs and metadata.",
    )
    app_env: Literal["development", "test", "production"] = Field(
        default="development",
        description="Deployment environment for the current process.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Minimum log level emitted by structlog.",
    )
    log_json: bool = Field(
        default=True,
        description="Render structured logs as JSON when enabled.",
    )

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        description="API key used by the LLM client wrapper.",
    )
    llm_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Default model identifier for future LLM calls.",
    )
    llm_max_tokens: int = Field(
        default=8192,
        ge=256,
        description="Maximum tokens per LLM response.",
    )

    github_token: SecretStr | None = Field(
        default=None,
        description="Token used by future GitHub integrations.",
    )
    github_org: str | None = Field(
        default=None,
        description="Default GitHub organization for future integrations.",
    )

    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for the message bus.",
    )
    redis_stream_prefix: str = Field(
        default="swarm",
        description="Prefix applied to all Redis stream names.",
    )
    redis_stream_block_ms: int = Field(
        default=1000,
        ge=0,
        description="Blocking poll time for Redis stream consumers.",
    )
    redis_consumer_batch_size: int = Field(
        default=10,
        ge=1,
        description="Maximum number of events read per consumer poll.",
    )
    redis_stream_maxlen: int = Field(
        default=10000,
        ge=100,
        description="Approximate max stream length before trimming.",
    )
    consumer_poll_delay_seconds: float = Field(
        default=0.1,
        ge=0.0,
        description="Sleep duration between consumer loops when idle.",
    )

    kubeconfig_path: str = Field(
        default="~/.kube/config",
        description="Path to the Kubernetes configuration file.",
    )
    k8s_namespace: str = Field(
        default="devops-swarm",
        description="Namespace used by cluster-scoped components.",
    )

    prometheus_url: str = Field(
        default="http://localhost:9090",
        description="Base URL for Prometheus queries.",
    )
    loki_url: str = Field(
        default="http://localhost:3100",
        description="Base URL for Loki queries.",
    )

    slack_webhook_url: SecretStr | None = Field(
        default=None,
        description="Slack webhook for future approval notifications.",
    )
    slack_approval_channel: str = Field(
        default="#devops-approvals",
        description="Slack channel where approval requests will be posted.",
    )

    vcluster_binary_path: str = Field(
        default="/usr/local/bin/vcluster",
        description="Filesystem path to the vcluster binary.",
    )
    sandbox_max_age_minutes: int = Field(
        default=30,
        ge=1,
        description="Maximum lifetime for ephemeral sandbox clusters.",
    )

    dry_run_mode: bool = Field(
        default=True,
        description="Global safety switch for write operations.",
    )
    require_human_approval: bool = Field(
        default=True,
        description="Require human approval before any live change.",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def stream_name(self, agent_name: str) -> str:
        """Build the canonical stream name for an agent."""

        return f"{self.redis_stream_prefix}:{agent_name}"

    @property
    def broadcast_stream(self) -> str:
        """Return the stream used for broadcast events."""

        return self.stream_name("broadcast")

    @property
    def dead_letter_stream(self) -> str:
        """Return the stream used for failed or malformed messages."""

        return self.stream_name("dlq")
