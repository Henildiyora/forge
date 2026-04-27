from __future__ import annotations

import pytest

from swarm.core.config import Settings
from swarm.core.exceptions import InsufficientEvidenceError
from swarm.core.llm import LLMClient, LLMResponse


class StubLLMProvider:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response

    async def complete(
        self,
        *,
        prompt: str,
        task_id: str,
        agent: str,
        expected_format: str,
    ) -> LLMResponse:
        del prompt, task_id, agent, expected_format
        return self.response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_client_accepts_response_with_evidence_and_confidence(
    test_settings: Settings,
) -> None:
    client = LLMClient(
        test_settings,
        provider=StubLLMProvider(
            LLMResponse(
                data={"summary": "ready"},
                evidence=["Scanned deployment manifest."],
                confidence=0.91,
                raw_text="ready",
            )
        ),
    )

    response = await client.complete(
        prompt="Summarize the deployment plan.",
        task_id="llm-1",
        agent="captain",
        expected_format="json",
    )

    assert response.data["summary"] == "ready"
    assert response.confidence == 0.91


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_client_rejects_missing_evidence(
    test_settings: Settings,
) -> None:
    client = LLMClient(
        test_settings,
        provider=StubLLMProvider(
            LLMResponse(
                data={"summary": "ready"},
                evidence=[],
                confidence=0.95,
            )
        ),
    )

    with pytest.raises(InsufficientEvidenceError):
        await client.complete(
            prompt="Summarize the deployment plan.",
            task_id="llm-2",
            agent="captain",
            expected_format="json",
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_client_rejects_low_confidence(
    test_settings: Settings,
) -> None:
    client = LLMClient(
        test_settings,
        provider=StubLLMProvider(
            LLMResponse(
                data={"summary": "ready"},
                evidence=["Scanned deployment manifest."],
                confidence=0.2,
            )
        ),
    )

    with pytest.raises(InsufficientEvidenceError):
        await client.complete(
            prompt="Summarize the deployment plan.",
            task_id="llm-3",
            agent="captain",
            expected_format="json",
        )
