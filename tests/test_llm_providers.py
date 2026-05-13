from __future__ import annotations

import json

import httpx
import pytest

from forge.core.config import Settings
from forge.core.llm import LLMClient, OllamaProvider, OpenAICompatProvider


def _intent_envelope() -> dict[str, object]:
    return {
        "data": {
            "wants_simplicity": True,
            "has_existing_infra": False,
            "mentioned_scale": None,
            "mentioned_cloud": None,
            "mentioned_tools": [],
            "is_greenfield": True,
            "confidence": 0.8,
        },
        "evidence": ["parsed from scan"],
        "confidence": 0.82,
    }


@pytest.mark.asyncio
async def test_ollama_provider_calls_api_chat_with_json_format() -> None:
    inner = json.dumps(_intent_envelope())

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/chat")
        body = json.loads(request.content.decode())
        assert body.get("format") == "json"
        assert body.get("stream") is False
        assert body["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={"model": "m", "message": {"role": "assistant", "content": inner}},
        )

    transport = httpx.MockTransport(handler)
    prov = OllamaProvider(base_url="http://ollama.test", model="m", transport=transport)
    resp = await prov.complete(
        prompt="Interpret the user's deployment goal into a structured UserIntent JSON object.\n"
        "USER_INPUT: deploy to docker\n",
        task_id="t1",
        agent="conversation_engine",
        expected_format="json",
    )
    assert resp.data.get("wants_simplicity") is True
    assert resp.evidence
    assert resp.confidence >= 0.5


@pytest.mark.asyncio
async def test_openai_compat_provider_uses_chat_completions() -> None:
    inner = json.dumps(_intent_envelope())

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/v1/chat/completions" in str(request.url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": inner}}]},
        )

    transport = httpx.MockTransport(handler)
    prov = OpenAICompatProvider(
        base_url="http://llama.test",
        model="local",
        provider_name="llamacpp",
        transport=transport,
    )
    resp = await prov.complete(
        prompt="Interpret the user's deployment goal into a structured UserIntent JSON object.\n"
        "USER_INPUT: deploy to docker\n",
        task_id="t1",
        agent="conversation_engine",
        expected_format="json",
    )
    assert resp.data.get("is_greenfield") is True


@pytest.mark.asyncio
async def test_llm_client_falls_back_to_heuristic_when_ollama_errors(
    test_settings: Settings,
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"error": "nope"}))
    ollama = OllamaProvider(base_url="http://ollama.test", model="m", transport=transport)
    settings = test_settings.model_copy(
        update={
            "llm_backend": "ollama",
            "ollama_base_url": "http://ollama.test",
            "ollama_model": "m",
        }
    )
    notices: list[str] = []

    def _note(msg: str) -> None:
        notices.append(msg)

    client = LLMClient(settings, provider=ollama, on_provider_fallback_notice=_note)
    prompt = (
        "Interpret the user's deployment goal into a structured UserIntent JSON object.\n"
        "USER_INPUT: deploy with kubernetes\n"
    )
    resp = await client.complete(
        prompt=prompt,
        task_id="t1",
        agent="conversation_engine",
        expected_format="json",
    )
    assert notices and "heuristic" in notices[0].lower()
    assert "mentioned_tools" in resp.data or "wants_simplicity" in resp.data
