from __future__ import annotations

import pytest

from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.conversation.engine import ConversationEngine
from forge.conversation.questions import ClarificationQuestion
from forge.core.config import Settings
from forge.core.llm import LLMClient, LLMResponse


def _scan_result() -> CodebaseScanResult:
    return CodebaseScanResult(
        project_path="/tmp/project",
        language="python",
        framework="fastapi",
        entry_point="main.py",
        port=8000,
        env_vars=[],
        database_connections=[],
        service_count=1,
        detected_infra=[],
        has_existing_infra=False,
        file_count=2,
        evidence=["fixture"],
        confidence=0.9,
    )


@pytest.mark.asyncio
async def test_conversation_engine_asks_for_cloud_when_serverless_is_requested() -> None:
    engine = ConversationEngine(LLMClient(Settings(llm_backend="heuristic")), _scan_result())

    intent = await engine.interpret_intent("Deploy this API as a serverless app")

    assert engine.needs_clarification(intent) is True
    question = await engine.next_clarification_question(intent)
    assert question.question_key == "cloud_provider"


@pytest.mark.asyncio
async def test_conversation_engine_builds_a_confirmable_decision() -> None:
    engine = ConversationEngine(LLMClient(Settings(llm_backend="heuristic")), _scan_result())

    intent = await engine.interpret_intent(
        "I want a production kubernetes deployment for this service"
    )
    selection = engine.select_strategy(intent)
    decision = await engine.build_recommendation(selection.strategy, "production kubernetes")

    assert decision.strategy.value == "kubernetes"
    assert "FORGE recommends" in decision.reasoning


@pytest.mark.asyncio
async def test_conversation_engine_asks_docker_vs_k8s_on_conflicting_signals() -> None:
    scan = _scan_result().model_copy(update={"service_count": 7})
    engine = ConversationEngine(LLMClient(Settings(llm_backend="heuristic")), scan)

    intent = await engine.interpret_intent("I just need a Dockerfile for Docker Hub")

    assert engine.needs_clarification(intent) is True
    question = await engine.next_clarification_question(intent)
    assert question.question_key == "deployment_strategy_preference"
    engine.record_answer(question, "docker_compose")
    selection = engine.select_strategy(intent)
    assert selection.strategy.value == "docker_compose"


@pytest.mark.asyncio
async def test_conversation_engine_uses_safe_defaults_for_unsure_answers() -> None:
    scan = _scan_result().model_copy(update={"service_count": 4})
    engine = ConversationEngine(LLMClient(Settings(llm_backend="heuristic")), scan)

    intent = await engine.interpret_intent("I do not know how to deploy this, please help")
    strategy_question = ClarificationQuestion(
        question_key="deployment_strategy_preference",
        prompt="Pick one",
        options=[],
        rationale="test",
    )
    engine.record_answer(strategy_question, "unknown")
    selection = engine.select_strategy(intent)

    assert selection.strategy.value == "kubernetes"


@pytest.mark.asyncio
async def test_low_confidence_goal_returns_schema_valid_clarification_question() -> None:
    scan = _scan_result().model_copy(update={"service_count": 3})
    engine = ConversationEngine(LLMClient(Settings(llm_backend="heuristic")), scan)

    intent = await engine.interpret_intent("I don't know what to deploy yet, please suggest")
    question = await engine.next_clarification_question(intent)

    assert question.question_key == "deployment_strategy_preference"
    assert question.rationale
    assert len(question.options) >= 2


@pytest.mark.asyncio
async def test_conversation_engine_falls_back_on_malformed_clarification_payload() -> None:
    scan = _scan_result().model_copy(update={"service_count": 3})
    llm = LLMClient(Settings(llm_backend="heuristic"))
    engine = ConversationEngine(llm, scan)
    intent = await engine.interpret_intent("I don't know, suggest a deployment path")

    async def _bad_complete(**kwargs: object) -> LLMResponse:
        del kwargs
        return LLMResponse(data={"summary": "bad-shape"}, evidence=["test"], confidence=0.9)

    engine.llm.complete = _bad_complete  # type: ignore[method-assign]
    question = await engine.next_clarification_question(intent)

    assert question.question_key == "deployment_strategy_preference"
    assert any(option.value == "unknown" for option in question.options)
