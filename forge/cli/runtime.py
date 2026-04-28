from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from forge.core.config import Settings
from forge.core.logging import configure_logging
from forge.core.message_bus import InMemoryStreamClient, MessageBus

T = TypeVar("T")


LocalStreamClient = InMemoryStreamClient
"""Backwards-compatible alias for the in-memory stream client."""


def cli_settings() -> Settings:
    """Load settings suitable for local CLI usage."""

    settings = Settings().model_copy(update={"log_json": False, "log_level": "ERROR"})
    configure_logging(settings)
    return settings


def local_message_bus(settings: Settings) -> MessageBus:
    """Create an in-memory message bus for direct workflow execution.

    No Redis required: events are buffered in-process so the CLI works fully
    offline. Equivalent to :meth:`MessageBus.in_memory`.
    """

    return MessageBus.in_memory(settings)


def run_async(awaitable: Coroutine[Any, Any, T]) -> T:
    """Run an async operation from a synchronous CLI command."""

    return asyncio.run(awaitable)
