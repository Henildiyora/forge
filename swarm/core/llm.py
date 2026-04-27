from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

import structlog
from pydantic import BaseModel, Field

from swarm.core.config import Settings
from swarm.core.exceptions import InsufficientEvidenceError


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


class LLMClient:
    """Shared wrapper for evidence-constrained LLM access."""

    def __init__(
        self,
        settings: Settings,
        provider: SupportsLLMProvider | None = None,
    ):
        self.settings = settings
        self.provider = provider
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

        if self.provider is None:
            self.logger.error(
                "llm_provider_not_configured",
                task_id=task_id,
                agent=agent,
                expected_format=expected_format,
                prompt_length=len(prompt),
            )
            raise NotImplementedError(
                "LLM provider integration is intentionally deferred "
                "until the shared foundation is validated."
            )

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


def _coerce_response(raw_response: LLMResponse | Mapping[str, object]) -> LLMResponse:
    if isinstance(raw_response, LLMResponse):
        return raw_response
    return LLMResponse.model_validate(cast(dict[str, object], dict(raw_response)))
