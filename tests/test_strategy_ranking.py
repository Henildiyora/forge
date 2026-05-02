from __future__ import annotations

from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.conversation.strategy_ranking import rank_strategies, resolve_strategy_choice
from forge.conversation.strategy_selector import UserIntentLike
from forge.core.strategies import DeploymentStrategy


def _scan() -> CodebaseScanResult:
    return CodebaseScanResult(
        project_path="/tmp/p",
        language="python",
        framework="fastapi",
        entry_point="main.py",
        port=8000,
        env_vars=[],
        database_connections=[],
        service_count=1,
        detected_infra=[],
        has_existing_infra=False,
        file_count=3,
        evidence=["x"],
        confidence=0.9,
    )


def test_rank_boosts_serverless_for_lambda_goal() -> None:
    intent = UserIntentLike(
        wants_simplicity=True,
        has_existing_infra=False,
        mentioned_scale="small",
        mentioned_cloud="aws",
        mentioned_tools=["lambda"],
        is_greenfield=True,
        confidence=0.8,
    )
    ranked = rank_strategies(_scan(), intent, None, top_n=3, goal_lower="deploy to aws lambda")
    assert ranked[0].strategy == DeploymentStrategy.SERVERLESS


def test_resolve_choice_by_index() -> None:
    intent = UserIntentLike()
    ranked = rank_strategies(_scan(), intent, None, top_n=3, goal_lower="")
    picked = resolve_strategy_choice("2", ranked)
    assert picked is not None
    assert picked.strategy == ranked[1].strategy
