"""Assembles the tools, agents, and sandbox used by a single swarm run."""

from __future__ import annotations

from pathlib import Path

from swarm.agents.code_analysis import CodeAnalysisAgent
from swarm.agents.monitoring import MonitoringAgent
from swarm.agents.ops import OpsAgent
from swarm.config import Settings, get_settings
from swarm.dryrun.docker_sandbox import DockerSandbox
from swarm.llm import AnthropicClient
from swarm.tools.github import FixtureCommitSource, LiveGitHubCommitSource, build_github_tool
from swarm.tools.prometheus import (
    PrometheusClient,
    QueryRangeArgs,
    QueryRangeResult,
    build_prometheus_tool,
)
from swarm.tools.registry import Tool, ToolRegistry


class _PrometheusLike:
    """Structural stand-in so FixturePrometheusClient type-checks at runtime."""

    base_url: str

    def query_range(self, args: QueryRangeArgs) -> QueryRangeResult:
        raise NotImplementedError

    def close(self) -> None:
        return None


class SwarmRuntime:
    """Concrete dependencies injected into the LangGraph nodes."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        commit_fixture: Path | None = None,
        commit_source_name: str = "fixture",
        prometheus_client: PrometheusClient | _PrometheusLike | None = None,
        sandbox: DockerSandbox | None = None,
        llm: AnthropicClient | None = None,
        skip_llm: bool = False,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = ToolRegistry()

        self.prometheus = prometheus_client or PrometheusClient(
            base_url=self.settings.prometheus_url,
            timeout_seconds=self.settings.prometheus_timeout_seconds,
        )
        if isinstance(self.prometheus, PrometheusClient):
            self.registry.register(build_prometheus_tool(self.prometheus))
        else:
            self.registry.register(
                Tool(
                    name="prometheus.query_range",
                    description="Evaluate PromQL over a time window (fixture replay).",
                    args_model=QueryRangeArgs,
                    result_model=QueryRangeResult,
                    handler=self.prometheus.query_range,
                )
            )

        if commit_source_name == "live":
            token = (
                self.settings.github_token.get_secret_value()
                if self.settings.has_github
                else None
            )
            source = LiveGitHubCommitSource(token=token)
        else:
            if commit_fixture is None:
                raise ValueError("commit_fixture is required when commit_source='fixture'")
            source = FixtureCommitSource(commit_fixture)
        self.registry.register(build_github_tool(source))

        if skip_llm:
            self.llm = None
        elif llm is not None:
            self.llm = llm
        elif self.settings.has_anthropic:
            self.llm = AnthropicClient(
                api_key=self.settings.anthropic_api_key.get_secret_value(),  # type: ignore[union-attr]
                model=self.settings.anthropic_model,
                max_tokens=self.settings.anthropic_max_tokens,
                timeout_seconds=self.settings.anthropic_timeout_seconds,
            )
        else:
            self.llm = None

        self.monitoring = MonitoringAgent(self.registry)
        self.code_analysis = CodeAnalysisAgent(self.registry)
        self.ops = OpsAgent(self.llm)
        self.sandbox = sandbox or DockerSandbox(self.settings)

    def close(self) -> None:
        """Release owned network resources."""

        self.prometheus.close()
        if self.llm is not None:
            self.llm.close()
