from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol

from pydantic import BaseModel, Field

from forge.core.config import Settings


class CheckpointRecord(BaseModel):
    """Persisted workflow checkpoint used for approvals and remediation resume."""

    task_id: str = Field(description="Workflow task identifier.")
    workflow_type: str = Field(description="Workflow category such as build or incident.")
    current_step: str = Field(description="Step where execution paused.")
    state: dict[str, object] = Field(
        default_factory=dict,
        description="Serialized workflow payload required to resume execution.",
    )
    approval_request_id: str | None = Field(
        default=None,
        description="Linked approval request identifier when one exists.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SupportsCheckpointBackend(Protocol):
    """Key-value persistence interface for workflow checkpoints."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...

    async def delete(self, key: str) -> None: ...


class InMemoryCheckpointBackend:
    """In-memory checkpoint backend used in local CLI and tests."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = Lock()

    async def get(self, key: str) -> str | None:
        with self._lock:
            return self._data.get(key)

    async def set(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value

    async def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class FileCheckpointBackend:
    """Filesystem-backed checkpoint backend used when Redis is unavailable."""

    def __init__(self) -> None:
        self._root = Path(tempfile.gettempdir()) / "forge-checkpoints"
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe_name = key.replace("/", "_").replace(":", "_")
        return self._root / f"{safe_name}.json"

    async def get(self, key: str) -> str | None:
        path = self._path(key)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    async def set(self, key: str, value: str) -> None:
        self._path(key).write_text(value, encoding="utf-8")

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()


class RedisCheckpointBackend:
    """Redis-backed checkpoint backend used when a live Redis client is available."""

    def __init__(self, settings: Settings) -> None:
        from redis.asyncio import Redis

        self._client = Redis.from_url(settings.redis_url, decode_responses=True)

    async def get(self, key: str) -> str | None:
        value = await self._client.get(key)
        return value if isinstance(value, str) else None

    async def set(self, key: str, value: str) -> None:
        await self._client.set(key, value)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)


class CheckpointStore:
    """Stores and retrieves workflow checkpoints for approval-driven resume."""

    def __init__(
        self,
        settings: Settings,
        backend: SupportsCheckpointBackend | None = None,
    ) -> None:
        self.settings = settings
        if backend is not None:
            self.backend = backend
        else:
            self.backend = FileCheckpointBackend()

    def _key(self, task_id: str) -> str:
        return f"{self.settings.checkpoint_namespace}:{task_id}"

    async def save(self, record: CheckpointRecord) -> None:
        updated = record.model_copy(update={"updated_at": datetime.now(UTC)})
        await self.backend.set(
            self._key(record.task_id),
            json.dumps(updated.model_dump(mode="json")),
        )

    async def load(self, task_id: str) -> CheckpointRecord | None:
        payload = await self.backend.get(self._key(task_id))
        if payload is None:
            return None
        return CheckpointRecord.model_validate(json.loads(payload))

    async def delete(self, task_id: str) -> None:
        await self.backend.delete(self._key(task_id))
