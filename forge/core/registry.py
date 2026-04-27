from __future__ import annotations

from typing import TYPE_CHECKING

from forge.core.exceptions import ConfigurationError

if TYPE_CHECKING:
    from forge.agents.base import BaseAgent


class AgentRegistry:
    """Registry of available agent classes keyed by agent name."""

    def __init__(self) -> None:
        self._registry: dict[str, type[BaseAgent]] = {}

    def register(self, agent_class: type[BaseAgent]) -> None:
        agent_name = getattr(agent_class, "agent_name", "")
        if not agent_name:
            raise ConfigurationError("Agent classes must define a non-empty agent_name.")
        self._registry[agent_name] = agent_class

    def get(self, agent_name: str) -> type[BaseAgent]:
        try:
            return self._registry[agent_name]
        except KeyError as exc:
            raise ConfigurationError(f"Agent {agent_name} is not registered.") from exc

    def available_agents(self) -> list[str]:
        return sorted(self._registry)
