from __future__ import annotations

from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.core.strategies import DeploymentStrategy


def intent_prompt(scan_result: CodebaseScanResult, user_input: str) -> str:
    """Build the prompt used to interpret free-form deployment intent."""

    return (
        "Interpret the user's deployment goal into a structured UserIntent JSON object.\n"
        f"SCAN_RESULT: {scan_result.model_dump(mode='json')}\n"
        f"USER_INPUT: {user_input}\n"
        "Return JSON only."
    )


def clarification_prompt(scan_result: CodebaseScanResult, missing_hint: str) -> str:
    """Build the prompt used to request a single clarification question."""

    return (
        "Return a single ClarificationQuestion JSON object.\n"
        f"SCAN_RESULT: {scan_result.model_dump(mode='json')}\n"
        f"MISSING_HINT: {missing_hint}\n"
        "The question must have at most 4 options and include a 'Not sure' option."
    )


def recommendation_prompt(
    strategy: DeploymentStrategy,
    scan_result: CodebaseScanResult,
    goal: str,
) -> str:
    """Build the prompt used to explain the recommended strategy."""

    return (
        "Return a DeploymentDecision JSON explanation payload.\n"
        f"STRATEGY: {strategy.value}\n"
        f"SCAN_RESULT: {scan_result.model_dump(mode='json')}\n"
        f"USER_GOAL: {goal}\n"
        "Return JSON only with reasoning, requirements, what_will_be_generated, "
        "and estimated_setup_time."
    )
