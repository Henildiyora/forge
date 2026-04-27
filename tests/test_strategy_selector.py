from __future__ import annotations

from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.conversation.strategy_selector import (
    StrategySelectionContext,
    UserIntentLike,
    select_strategy,
)


def _scan_result(*, has_existing_infra: bool = False, service_count: int = 1) -> CodebaseScanResult:
    return CodebaseScanResult(
        project_path="/tmp/project",
        language="python",
        framework="fastapi",
        entry_point="main.py",
        port=8000,
        env_vars=[],
        database_connections=[],
        service_count=service_count,
        detected_infra=["kubernetes"] if has_existing_infra else [],
        has_existing_infra=has_existing_infra,
        file_count=3,
        evidence=["fixture"],
        confidence=0.9,
    )


def test_selector_prefers_extend_existing_for_brownfield() -> None:
    result = select_strategy(
        _scan_result(has_existing_infra=True),
        UserIntentLike(has_existing_infra=True, is_greenfield=False),
    )
    assert result.strategy.value == "extend_existing"


def test_selector_prefers_serverless_for_small_aws_goal() -> None:
    result = select_strategy(
        _scan_result(service_count=1),
        UserIntentLike(
            wants_simplicity=True,
            mentioned_scale="small",
            mentioned_cloud="aws",
            mentioned_tools=["lambda"],
        ),
    )
    assert result.strategy.value == "serverless"


def test_selector_prefers_kubernetes_for_multi_service_projects() -> None:
    result = select_strategy(
        _scan_result(service_count=4),
        UserIntentLike(mentioned_scale="large", mentioned_tools=["kubernetes"]),
        StrategySelectionContext(service_count_hint=4),
    )
    assert result.strategy.value == "kubernetes"
