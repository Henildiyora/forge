from __future__ import annotations

from pydantic import BaseModel, Field

from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.core.strategies import DeploymentStrategy


class StrategySelectionContext(BaseModel):
    """Deterministic inputs used by FORGE strategy selection."""

    service_count_hint: int | None = Field(
        default=None,
        description="Clarified or inferred service count if the user supplied it.",
    )
    preferred_cloud: str | None = Field(
        default=None,
        description="Cloud chosen or clarified by the user when relevant.",
    )
    wants_local_only: bool = Field(
        default=False,
        description="Whether the user explicitly prefers local-only execution.",
    )
    wants_cicd_only: bool = Field(
        default=False,
        description="Whether the user only wants a CI/CD pipeline.",
    )


class StrategySelectionResult(BaseModel):
    """Deterministic result returned by the strategy selector."""

    strategy: DeploymentStrategy = Field(description="Selected deployment strategy.")
    reason: str = Field(description="Short deterministic explanation for the choice.")


def select_strategy(
    scan_result: CodebaseScanResult,
    intent: UserIntentLike,
    context: StrategySelectionContext | None = None,
) -> StrategySelectionResult:
    """Select exactly one deployment strategy without calling an LLM."""

    selection = context or StrategySelectionContext()
    effective_service_count = selection.service_count_hint or scan_result.service_count
    mentioned_tools = {tool.lower() for tool in intent.mentioned_tools}

    if selection.wants_cicd_only or "cicd_only" in mentioned_tools or "pipeline" in mentioned_tools:
        return StrategySelectionResult(
            strategy=DeploymentStrategy.CICD_ONLY,
            reason="User asked for automation only, without changing the deployment target.",
        )
    if scan_result.has_existing_infra and intent.has_existing_infra:
        return StrategySelectionResult(
            strategy=DeploymentStrategy.EXTEND_EXISTING,
            reason=(
                "The repository already contains deployment infrastructure "
                "and the user wants to build on top of it."
            ),
        )
    if (
        "lambda" in mentioned_tools
        or "cloud run" in mentioned_tools
        or intent.mentioned_cloud in {"aws", "gcp"}
        and intent.mentioned_scale == "small"
    ):
        return StrategySelectionResult(
            strategy=DeploymentStrategy.SERVERLESS,
            reason=(
                "The goal and cloud preference point to a stateless, "
                "serverless deployment target."
            ),
        )
    if selection.wants_local_only or intent.wants_simplicity:
        if effective_service_count <= 2 and intent.mentioned_scale != "large":
            return StrategySelectionResult(
                strategy=DeploymentStrategy.DOCKER_COMPOSE,
                reason=(
                    "The project is small enough to start with a simple local "
                    "or single-host deployment."
                ),
            )
    if effective_service_count > 1 or intent.mentioned_scale in {"medium", "large"}:
        return StrategySelectionResult(
            strategy=DeploymentStrategy.KUBERNETES,
            reason=(
                "The project complexity and deployment requirements justify "
                "Kubernetes orchestration."
            ),
        )
    if scan_result.has_existing_infra:
        return StrategySelectionResult(
            strategy=DeploymentStrategy.EXTEND_EXISTING,
            reason=(
                "Existing infrastructure was detected, so FORGE will extend it "
                "instead of replacing it."
            ),
        )
    return StrategySelectionResult(
        strategy=DeploymentStrategy.DOCKER_COMPOSE,
        reason=(
            "The repository looks like a single-service application and a "
            "simple deployment path is the best fit."
        ),
    )


class UserIntentLike(BaseModel):
    """Local protocol-like model used to avoid import cycles in selector tests."""

    wants_simplicity: bool = False
    has_existing_infra: bool = False
    mentioned_scale: str | None = None
    mentioned_cloud: str | None = None
    mentioned_tools: list[str] = Field(default_factory=list)
    is_greenfield: bool = True
    confidence: float = 0.0
