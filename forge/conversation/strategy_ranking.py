"""Rank deployment strategies for Manager-led forge build."""

from __future__ import annotations

from pydantic import BaseModel, Field

from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.conversation.strategy_selector import StrategySelectionContext, UserIntentLike
from forge.core.strategies import DeploymentStrategy


class ScoredStrategy(BaseModel):
    """One ranked deployment option shown to the user."""

    strategy: DeploymentStrategy
    score: float = Field(ge=0.0, le=100.0)
    reason: str = Field(description="One-line why this option fits.")
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    when_to_use: str = Field(description="Plain-language when this path is best.")
    migration_path: str = Field(description="What to move to when you outgrow this.")


def rank_strategies(
    scan_result: CodebaseScanResult,
    intent: UserIntentLike,
    context: StrategySelectionContext | None = None,
    *,
    top_n: int = 3,
    goal_lower: str = "",
) -> list[ScoredStrategy]:
    """Return up to ``top_n`` distinct strategies, highest score first."""

    ctx = context or StrategySelectionContext()
    effective_services = ctx.service_count_hint or scan_result.service_count
    mentioned = {t.lower() for t in intent.mentioned_tools}
    scores: dict[DeploymentStrategy, float] = {}
    for strat in DeploymentStrategy:
        scores[strat] = _score_one(strat, scan_result, intent, ctx, effective_services, mentioned)
    gl = goal_lower.lower()
    if "lambda" in gl or "serverless" in gl or "cloud run" in gl:
        scores[DeploymentStrategy.SERVERLESS] = scores.get(DeploymentStrategy.SERVERLESS, 0.0) + 35.0
    if "kubernetes" in gl or " k8s" in gl or "k8s " in gl:
        scores[DeploymentStrategy.KUBERNETES] = scores.get(DeploymentStrategy.KUBERNETES, 0.0) + 30.0
    if "compose" in gl or "docker compose" in gl:
        scores[DeploymentStrategy.DOCKER_COMPOSE] = scores.get(
            DeploymentStrategy.DOCKER_COMPOSE, 0.0
        ) + 25.0
    for key in scores:
        scores[key] = min(100.0, scores[key])

    ordered = sorted(scores.items(), key=lambda item: -item[1])
    seen: set[DeploymentStrategy] = set()
    out: list[ScoredStrategy] = []
    for strat, score in ordered:
        if strat in seen:
            continue
        seen.add(strat)
        out.append(_to_scored(strat, score, scan_result, effective_services, intent))
        if len(out) >= top_n:
            break
    return out


def _score_one(
    strategy: DeploymentStrategy,
    scan: CodebaseScanResult,
    intent: UserIntentLike,
    ctx: StrategySelectionContext,
    effective_services: int,
    mentioned: set[str],
) -> float:
    s = 40.0
    if strategy == DeploymentStrategy.DOCKER_COMPOSE:
        s += 25 if intent.wants_simplicity or ctx.wants_local_only else 0
        s += 20 if effective_services <= 2 else 5
        s -= 15 if effective_services > 3 else 0
        s += 10 if "docker" in mentioned or "compose" in mentioned else 0
    elif strategy == DeploymentStrategy.KUBERNETES:
        s += 20 if effective_services > 1 else 5
        s += 15 if intent.mentioned_scale in {"medium", "large"} else 0
        s += 12 if "kubernetes" in mentioned or "k8s" in mentioned else 0
        s -= 10 if intent.wants_simplicity and effective_services <= 1 else 0
    elif strategy == DeploymentStrategy.SERVERLESS:
        s += 25 if "serverless" in mentioned or "lambda" in mentioned else 0
        s += 15 if intent.mentioned_cloud in {"aws", "gcp"} else 0
        s -= 20 if effective_services > 2 else 0
    elif strategy == DeploymentStrategy.CICD_ONLY:
        s += 20 if ctx.wants_cicd_only or "pipeline" in mentioned else 0
        s += 8 if "github" in mentioned or "gitlab" in mentioned else 0
    elif strategy == DeploymentStrategy.EXTEND_EXISTING:
        s += 35 if scan.has_existing_infra and intent.has_existing_infra else 0
        s -= 25 if not scan.has_existing_infra else 0
    return max(0.0, min(100.0, s))


def _to_scored(
    strategy: DeploymentStrategy,
    score: float,
    scan: CodebaseScanResult,
    effective_services: int,
    intent: UserIntentLike,
) -> ScoredStrategy:
    fw = scan.framework or "unknown"
    if strategy == DeploymentStrategy.DOCKER_COMPOSE:
        return ScoredStrategy(
            strategy=strategy,
            score=score,
            reason=f"Fastest path for a {fw} app on one machine or a laptop.",
            pros=[
                "Minimal moving parts",
                "Great for learning and demos",
                "Works well with Docker Desktop",
            ],
            cons=["Not ideal for multi-node autoscaling", "You manage the host yourself"],
            when_to_use="You want to ship quickly or run locally with containers.",
            migration_path="Move to Kubernetes when you need replicas, autoscaling, or HA clusters.",
        )
    if strategy == DeploymentStrategy.KUBERNETES:
        return ScoredStrategy(
            strategy=strategy,
            score=score,
            reason=f"Best when this project ({effective_services} service signal) may grow in traffic or topology.",
            pros=["Autoscaling and rolling updates", "Standard for production microservices"],
            cons=["More concepts (pods, services, ingress)", "Needs a cluster or local k8s"],
            when_to_use="You expect production traffic, multiple services, or team-wide operations.",
            migration_path="Start with Compose for dev; promote the same images to Kubernetes later.",
        )
    if strategy == DeploymentStrategy.SERVERLESS:
        return ScoredStrategy(
            strategy=strategy,
            score=score,
            reason="Good fit for event-driven APIs and pay-per-use hosting.",
            pros=["Less server management", "Scales with demand automatically"],
            cons=["Cold starts and vendor limits", "Less control over runtime"],
            when_to_use="You have stateless HTTP handlers or small APIs on AWS/GCP.",
            migration_path="Move to containers if you need long-lived processes or GPUs.",
        )
    if strategy == DeploymentStrategy.CICD_ONLY:
        return ScoredStrategy(
            strategy=strategy,
            score=score,
            reason="Automate builds and tests without changing where the app runs today.",
            pros=["Low risk to existing deploys", "Improves quality gates"],
            cons=["Does not host the app by itself"],
            when_to_use="You already deploy elsewhere and only need pipelines.",
            migration_path="Add Docker/Kubernetes artifacts in a later iteration.",
        )
    return ScoredStrategy(
        strategy=strategy,
        score=score,
        reason="Extend what is already in the repo instead of replacing it.",
        pros=["Respects existing IaC and conventions", "Lower churn for teams"],
        cons=["Requires understanding current platform setup"],
        when_to_use="The scan detected existing deployment files and you want to build on them.",
        migration_path="Gradually introduce new services with the same platform patterns.",
    )


def resolve_strategy_choice(
    user_input: str,
    ranked: list[ScoredStrategy],
) -> ScoredStrategy | None:
    """Map free text or a menu index to one of the ranked strategies."""

    raw = user_input.strip()
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(ranked):
            return ranked[idx]
    lower = raw.lower()
    if "compose" in lower or "docker compose" in lower:
        for item in ranked:
            if item.strategy == DeploymentStrategy.DOCKER_COMPOSE:
                return item
    if "k8s" in lower or "kubernetes" in lower or "kube" in lower:
        for item in ranked:
            if item.strategy == DeploymentStrategy.KUBERNETES:
                return item
    if "serverless" in lower or "lambda" in lower or "cloud run" in lower:
        for item in ranked:
            if item.strategy == DeploymentStrategy.SERVERLESS:
                return item
    if "cicd" in lower or "pipeline" in lower or "ci" in lower:
        for item in ranked:
            if item.strategy == DeploymentStrategy.CICD_ONLY:
                return item
    if "extend" in lower or "existing" in lower:
        for item in ranked:
            if item.strategy == DeploymentStrategy.EXTEND_EXISTING:
                return item
    return None
