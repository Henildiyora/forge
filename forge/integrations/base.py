from __future__ import annotations

from abc import ABC, abstractmethod


class BaseIntegration(ABC):
    """Base interface for third-party service integrations."""

    name: str

    @abstractmethod
    async def health_check(self) -> dict[str, object]:
        """Return a health payload for the integration."""
