from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from typing import Protocol, cast

import httpx
import structlog
from pydantic import BaseModel, Field

from forge.core.config import Settings
from forge.core.exceptions import InsufficientEvidenceError


class LLMResponse(BaseModel):
    """Structured response returned by the shared LLM wrapper."""

    data: dict[str, object] = Field(
        default_factory=dict,
        description="Validated payload returned by the model provider.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence citations supporting the model output.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Model confidence score when available.",
    )
    raw_text: str | None = Field(
        default=None,
        description="Provider raw text for debugging and audit trails.",
    )


class LLMBackend(str, Enum):
    """Supported LLM backends for FORGE."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OLLAMA = "ollama"
    LLAMACPP = "llamacpp"
    HEURISTIC = "heuristic"


class SupportsLLMProvider(Protocol):
    """Protocol for pluggable providers used by the shared LLM client."""

    async def complete(
        self,
        *,
        prompt: str,
        task_id: str,
        agent: str,
        expected_format: str,
    ) -> LLMResponse | Mapping[str, object]: ...


class HeuristicProvider:
    """Deterministic fallback provider used for local, offline FORGE workflows."""

    async def complete(
        self,
        *,
        prompt: str,
        task_id: str,
        agent: str,
        expected_format: str,
    ) -> LLMResponse:
        del task_id, agent, expected_format
        lower_prompt = prompt.lower()
        if "userintent" in lower_prompt or "structured intent" in lower_prompt:
            goal = _extract_user_goal(prompt)
            data = _heuristic_intent(goal)
            return LLMResponse(
                data=data,
                evidence=[f"Heuristic parsing matched goal text: {goal}"],
                confidence=0.84,
                raw_text=json.dumps(data),
            )
        if "clarificationquestion" in lower_prompt or "clarification question" in lower_prompt:
            data = _heuristic_question(lower_prompt)
            return LLMResponse(
                data=data,
                evidence=["Heuristic clarification routing used missing intent fields."],
                confidence=0.81,
                raw_text=json.dumps(data),
            )
        if "deploymentdecision" in lower_prompt or "strategy recommendation" in lower_prompt:
            data = _heuristic_recommendation(lower_prompt)
            return LLMResponse(
                data=data,
                evidence=["Heuristic recommendation text composed from selected strategy."],
                confidence=0.88,
                raw_text=json.dumps(data),
            )
        return LLMResponse(
            data={"summary": "Heuristic fallback response."},
            evidence=["Heuristic fallback handled the prompt."],
            confidence=0.75,
            raw_text="Heuristic fallback response.",
        )


class HTTPJSONProvider:
    """Minimal HTTP JSON backend for Ollama and llama.cpp style local servers."""

    def __init__(self, *, base_url: str, model: str, provider_name: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider_name = provider_name

    async def complete(
        self,
        *,
        prompt: str,
        task_id: str,
        agent: str,
        expected_format: str,
    ) -> LLMResponse:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/completions",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "task_id": task_id,
                    "agent": agent,
                    "expected_format": expected_format,
                },
            )
            response.raise_for_status()
            payload = response.json()
        return LLMResponse.model_validate(payload)


class OpenAIProvider:
    """OpenAI-compatible provider using the official Python SDK."""

    def __init__(self, *, api_key: str, model: str, max_tokens: int) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def complete(
        self,
        *,
        prompt: str,
        task_id: str,
        agent: str,
        expected_format: str,
    ) -> LLMResponse:
        response = await self._client.responses.create(
            model=self._model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are FORGE. Return JSON with keys: data, evidence, "
                        "confidence, raw_text. "
                        f"Expected format: {expected_format}."
                    ),
                },
                {
                    "role": "user",
                    "content": f"TASK_ID: {task_id}\nAGENT: {agent}\n\n{prompt}",
                },
            ],
            max_output_tokens=self._max_tokens,
        )
        raw_text = getattr(response, "output_text", "") or ""
        payload = json.loads(raw_text)
        return LLMResponse.model_validate(payload)


class AnthropicProvider:
    """Anthropic provider using the Messages HTTP API through httpx."""

    def __init__(self, *, api_key: str, model: str, max_tokens: int) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens

    async def complete(
        self,
        *,
        prompt: str,
        task_id: str,
        agent: str,
        expected_format: str,
    ) -> LLMResponse:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "system": (
                        "You are FORGE. Return JSON with keys: data, evidence, "
                        "confidence, raw_text. "
                        f"Expected format: {expected_format}."
                    ),
                    "messages": [
                        {
                            "role": "user",
                            "content": f"TASK_ID: {task_id}\nAGENT: {agent}\n\n{prompt}",
                        }
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
        content = payload.get("content", [])
        if isinstance(content, list):
            raw_text = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
        else:
            raw_text = ""
        return LLMResponse.model_validate(json.loads(raw_text))


class LLMClient:
    """Shared wrapper for evidence-constrained LLM access."""

    def __init__(
        self,
        settings: Settings,
        provider: SupportsLLMProvider | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider or self._init_provider()
        self.logger = structlog.get_logger().bind(component="llm_client")

    async def complete(
        self,
        *,
        prompt: str,
        task_id: str,
        agent: str,
        expected_format: str,
        minimum_evidence: int = 1,
        minimum_confidence: float = 0.5,
    ) -> LLMResponse:
        """Complete a prompt and enforce evidence and confidence requirements."""

        raw_response = await self.provider.complete(
            prompt=prompt,
            task_id=task_id,
            agent=agent,
            expected_format=expected_format,
        )
        response = _coerce_response(raw_response)
        self.validate_response(
            response,
            task_id=task_id,
            agent=agent,
            expected_format=expected_format,
            minimum_evidence=minimum_evidence,
            minimum_confidence=minimum_confidence,
        )
        self.logger.info(
            "llm_response_validated",
            task_id=task_id,
            agent=agent,
            expected_format=expected_format,
            evidence_count=len(response.evidence),
            confidence=response.confidence,
        )
        return response

    def validate_response(
        self,
        response: LLMResponse,
        *,
        task_id: str,
        agent: str,
        expected_format: str,
        minimum_evidence: int = 1,
        minimum_confidence: float = 0.5,
    ) -> None:
        """Raise if an LLM response does not satisfy evidence policy."""

        normalized_evidence = [
            evidence.strip() for evidence in response.evidence if evidence.strip()
        ]
        if len(normalized_evidence) < minimum_evidence:
            self.logger.error(
                "llm_response_insufficient_evidence",
                task_id=task_id,
                agent=agent,
                expected_format=expected_format,
                evidence_count=len(normalized_evidence),
                minimum_evidence=minimum_evidence,
            )
            raise InsufficientEvidenceError(
                f"Response for {agent} did not provide the required evidence count."
            )
        if response.confidence < minimum_confidence:
            self.logger.error(
                "llm_response_low_confidence",
                task_id=task_id,
                agent=agent,
                expected_format=expected_format,
                confidence=response.confidence,
                minimum_confidence=minimum_confidence,
            )
            raise InsufficientEvidenceError(
                f"Response for {agent} fell below the minimum confidence threshold."
            )
        if not response.data:
            self.logger.error(
                "llm_response_missing_payload",
                task_id=task_id,
                agent=agent,
                expected_format=expected_format,
            )
            raise InsufficientEvidenceError(
                f"Response for {agent} did not contain a structured payload."
            )

    def _init_provider(self) -> SupportsLLMProvider:
        backend = LLMBackend(self.settings.llm_backend)
        if backend == LLMBackend.HEURISTIC:
            return HeuristicProvider()
        if backend == LLMBackend.OLLAMA:
            return HTTPJSONProvider(
                base_url=self.settings.ollama_base_url,
                model=self.settings.ollama_model,
                provider_name="ollama",
            )
        if backend == LLMBackend.LLAMACPP:
            return HTTPJSONProvider(
                base_url=self.settings.llamacpp_base_url,
                model=self.settings.llamacpp_model,
                provider_name="llamacpp",
            )
        if backend == LLMBackend.OPENAI and self.settings.openai_api_key is not None:
            return OpenAIProvider(
                api_key=self.settings.openai_api_key.get_secret_value(),
                model=self.settings.llm_model,
                max_tokens=self.settings.llm_max_tokens,
            )
        if backend == LLMBackend.ANTHROPIC and self.settings.anthropic_api_key is not None:
            return AnthropicProvider(
                api_key=self.settings.anthropic_api_key.get_secret_value(),
                model=self.settings.llm_model,
                max_tokens=self.settings.llm_max_tokens,
            )
        return HeuristicProvider()


def _coerce_response(raw_response: LLMResponse | Mapping[str, object]) -> LLMResponse:
    if isinstance(raw_response, LLMResponse):
        return raw_response
    return LLMResponse.model_validate(cast(dict[str, object], dict(raw_response)))


def _extract_user_goal(prompt: str) -> str:
    marker = "USER_INPUT:"
    if marker in prompt:
        return prompt.split(marker, maxsplit=1)[1].strip()
    return prompt.strip()


def _heuristic_intent(goal: str) -> dict[str, object]:
    lower_goal = goal.lower()
    mentioned_cloud = None
    for cloud in ("aws", "gcp", "azure"):
        if cloud in lower_goal:
            mentioned_cloud = cloud
            break
    mentioned_scale = None
    if any(term in lower_goal for term in ("simple", "small", "local", "prototype")):
        mentioned_scale = "small"
    elif any(term in lower_goal for term in ("production", "users", "autoscale", "cluster")):
        mentioned_scale = "medium"
    elif any(term in lower_goal for term in ("many services", "microservices", "platform")):
        mentioned_scale = "large"
    tools = [
        tool
        for tool in (
            "docker",
            "kubernetes",
            "lambda",
            "cloud run",
            "serverless",
            "github actions",
        )
        if tool in lower_goal
    ]
    return {
        "wants_simplicity": any(term in lower_goal for term in ("simple", "local", "quick")),
        "has_existing_infra": any(
            term in lower_goal for term in ("existing", "already have", "brownfield", "current")
        ),
        "mentioned_scale": mentioned_scale,
        "mentioned_cloud": mentioned_cloud,
        "mentioned_tools": tools,
        "is_greenfield": not any(term in lower_goal for term in ("existing", "already have")),
        "confidence": 0.84,
    }


def _heuristic_question(prompt: str) -> dict[str, object]:
    if "service" in prompt:
        return {
            "question_key": "service_count",
            "prompt": "How many separate services does this project have?",
            "rationale": "Service count helps FORGE choose simple vs scalable deployment safely.",
            "options": [
                {
                    "key": "one",
                    "label": "Just one (monolith or single API)",
                    "value": "1",
                },
                {
                    "key": "small",
                    "label": "2-5 services (small microservices)",
                    "value": "3",
                },
                {
                    "key": "large",
                    "label": "6+ services (full microservices platform)",
                    "value": "6",
                },
                {
                    "key": "unsure",
                    "label": "Not sure — recommend the best option for me",
                    "value": "unknown",
                },
            ],
        }
    return {
        "question_key": "deployment_strategy_preference",
        "prompt": (
            "If you are unsure, start with Docker Compose for faster setup, "
            "or choose Kubernetes for stronger scaling. Which should FORGE generate?"
        ),
        "rationale": "Choosing a strategy first lets FORGE generate the right deployment files.",
        "options": [
            {
                "key": "docker_compose",
                "label": "Docker Compose (simple, faster to start)",
                "value": "docker_compose",
            },
            {
                "key": "kubernetes",
                "label": "Kubernetes (best for scale and resilience)",
                "value": "kubernetes",
            },
            {
                "key": "serverless",
                "label": "Serverless (event-driven and minimal ops)",
                "value": "serverless",
            },
            {
                "key": "unsure",
                "label": "Not sure — recommend the best option for me",
                "value": "unknown",
            },
        ],
    }


def _heuristic_recommendation(prompt: str) -> dict[str, object]:
    strategy = "kubernetes"
    if "docker_compose" in prompt:
        strategy = "docker_compose"
    elif "serverless" in prompt:
        strategy = "serverless"
    elif "extend_existing" in prompt:
        strategy = "extend_existing"
    elif "cicd_only" in prompt:
        strategy = "cicd_only"
    reasoning = (
        "FORGE recommends the "
        f"{strategy} strategy based on the project scan and goal. "
        "This is the best starting point for now, and you can move to Kubernetes later "
        "if traffic or service complexity grows. Watch for configuration drift between "
        "local and production environments."
    )
    return {
        "reasoning": reasoning,
        "requirements": [
            "Review the generated files before applying them to production",
            "Set required runtime credentials and environment variables",
            "Run local smoke tests before deploying",
        ],
        "what_will_be_generated": [
            "Dockerfile and/or compose file when containerization is selected",
            "Kubernetes manifests and CI workflow when Kubernetes is selected",
            "Supplemental deployment files for the selected target",
        ],
        "estimated_setup_time": "~10 minutes",
    }
