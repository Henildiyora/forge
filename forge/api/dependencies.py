from __future__ import annotations

from functools import lru_cache

from forge.cli.runtime import LocalStreamClient
from forge.core.checkpoints import CheckpointStore
from forge.core.config import Settings
from forge.core.message_bus import MessageBus


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return API settings."""

    return Settings()


@lru_cache(maxsize=1)
def get_bus() -> MessageBus:
    """Return a lightweight message bus for API event publishing."""

    settings = get_settings()
    return MessageBus(settings=settings, stream_client=LocalStreamClient())


@lru_cache(maxsize=1)
def get_checkpoint_store() -> CheckpointStore:
    """Return the checkpoint store used for approval and remediation resume."""

    return CheckpointStore(get_settings())
