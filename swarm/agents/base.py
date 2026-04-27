from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from swarm.core.config import Settings
from swarm.core.events import SwarmEvent
from swarm.core.exceptions import ConfigurationError
from swarm.core.message_bus import MessageBus


class BaseAgent(ABC):
    """Abstract base class for all DevOps Swarm agents."""

    agent_name: str
    version: str = "1.0.0"

    def __init__(self, settings: Settings, message_bus: MessageBus):
        if not self.agent_name:
            raise ConfigurationError("Every agent must define a non-empty agent_name.")
        self.settings = settings
        self.bus = message_bus
        self.logger = structlog.get_logger().bind(agent=self.agent_name, version=self.version)
        self._consumer_name = f"{self.agent_name}-{uuid4().hex[:8]}"
        self._running = False
        self._last_event_at: datetime | None = None

    @abstractmethod
    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        """Process an event and optionally return a follow-up event."""

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Return the current health status for the agent."""

    async def start(self) -> None:
        """Start consuming from the agent's assigned stream."""

        self._running = True
        self.logger.info("agent_started")
        await self.bus.consume(
            stream=self.bus.stream_for(self.agent_name),
            group=self.agent_name,
            consumer_name=self._consumer_name,
            handler=self._handle_event,
        )

    async def stop(self) -> None:
        """Signal the agent to stop consuming events."""

        self._running = False
        self.logger.info("agent_stopped")

    def default_health_status(self) -> dict[str, Any]:
        """Return a standard health payload shared by all agents."""

        return {
            "status": "running" if self._running else "idle",
            "version": self.version,
            "last_event_at": self._last_event_at.isoformat() if self._last_event_at else None,
        }

    async def _handle_event(self, event: SwarmEvent) -> None:
        """Wrap process_event with logging, metrics, and dead-letter handling."""

        self._last_event_at = datetime.now(UTC)
        self.logger.info("event_received", event_type=event.type.value, task_id=event.task_id)
        try:
            result = await self.process_event(event)
            if result is not None:
                await self.bus.publish(result)
                self.logger.info(
                    "event_published",
                    event_type=result.type.value,
                    task_id=result.task_id,
                )
        except Exception as exc:
            self.logger.error(
                "event_processing_failed",
                error=str(exc),
                event_type=event.type.value,
                task_id=event.task_id,
                exc_info=True,
            )
            await self.bus.publish_to_dlq(
                event,
                error=str(exc),
                stream=self.bus.stream_for(self.agent_name),
            )
