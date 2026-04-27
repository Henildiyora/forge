from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from typing import Any, TypeVar

from forge.core.config import Settings
from forge.core.logging import configure_logging
from forge.core.message_bus import MessageBus

T = TypeVar("T")


class LocalStreamClient:
    """Minimal in-process stream client for CLI workflow invocation."""

    async def xadd(
        self,
        name: str,
        fields: Mapping[str, str],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        del name, fields, maxlen, approximate
        return "0-0"

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> object:
        del name, groupname, id, mkstream
        return True

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        count: int = 1,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, Mapping[bytes | str, bytes | str]]]]]:
        del groupname, consumername, streams, count, block
        return []

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        del name, groupname
        return len(ids)

    async def close(self) -> None:
        return None


def cli_settings() -> Settings:
    """Load settings suitable for local CLI usage."""

    settings = Settings().model_copy(update={"log_json": False, "log_level": "ERROR"})
    configure_logging(settings)
    return settings


def local_message_bus(settings: Settings) -> MessageBus:
    """Create an in-memory message bus for direct workflow execution."""

    return MessageBus(settings=settings, stream_client=LocalStreamClient())


def run_async(awaitable: Coroutine[Any, Any, T]) -> T:
    """Run an async operation from a synchronous CLI command."""

    return asyncio.run(awaitable)
