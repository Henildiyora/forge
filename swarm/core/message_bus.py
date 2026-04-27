from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from json import JSONDecodeError
from typing import Protocol

import structlog
from pydantic import ValidationError

from swarm.core.config import Settings
from swarm.core.events import DeadLetterEnvelope, SwarmEvent
from swarm.core.exceptions import MessageBusError, MessageDecodingError


class SupportsRedisStreams(Protocol):
    """Protocol describing the Redis stream methods used by MessageBus."""

    async def xadd(
        self,
        name: str,
        fields: Mapping[str, str],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str: ...

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> object: ...

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        count: int = 1,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, Mapping[bytes | str, bytes | str]]]]]: ...

    async def xack(self, name: str, groupname: str, *ids: str) -> int: ...

    async def close(self) -> None: ...


class MessageBus:
    """Redis Streams wrapper for inter-agent communication."""

    def __init__(self, settings: Settings, stream_client: SupportsRedisStreams):
        self.settings = settings
        self._client = stream_client
        self._known_groups: set[tuple[str, str]] = set()
        self.logger = structlog.get_logger().bind(component="message_bus")

    @classmethod
    def from_settings(cls, settings: Settings) -> MessageBus:
        """Create a message bus using a real Redis asyncio client."""

        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise MessageBusError(
                "redis is not installed. Install project dependencies "
                "before creating the message bus."
            ) from exc

        client = Redis.from_url(settings.redis_url, decode_responses=False)
        return cls(settings=settings, stream_client=client)

    def stream_for(self, agent_name: str) -> str:
        """Return the canonical Redis stream name for an agent."""

        return self.settings.stream_name(agent_name)

    async def publish(self, event: SwarmEvent) -> str:
        """Publish an event to its target stream."""

        stream = (
            self.stream_for(event.target_agent)
            if event.target_agent
            else self.settings.broadcast_stream
        )
        message_id = await self._client.xadd(
            stream,
            self._serialize_event(event),
            maxlen=self.settings.redis_stream_maxlen,
            approximate=True,
        )
        self.logger.info(
            "event_published",
            stream=stream,
            event_type=event.type.value,
            task_id=event.task_id,
            message_id=message_id,
        )
        return message_id

    async def publish_to_dlq(
        self,
        event: SwarmEvent | None,
        *,
        error: str,
        stream: str,
        raw_message: dict[str, str] | None = None,
    ) -> str:
        """Publish a failed event or raw message to the dead letter queue."""

        envelope = DeadLetterEnvelope(
            stream=stream,
            error=error,
            original_event=event.model_dump(mode="json") if event is not None else None,
            raw_message=raw_message,
        )
        message_id = await self._client.xadd(
            self.settings.dead_letter_stream,
            {
                "stream": envelope.stream,
                "error": envelope.error,
                "original_event": json.dumps(envelope.original_event),
                "raw_message": json.dumps(envelope.raw_message),
                "failed_at": envelope.failed_at.isoformat(),
            },
            maxlen=self.settings.redis_stream_maxlen,
            approximate=True,
        )
        self.logger.error(
            "message_sent_to_dlq",
            stream=stream,
            error=error,
            message_id=message_id,
        )
        return message_id

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        """Acknowledge successful processing of a stream message."""

        await self._client.xack(stream, group, message_id)

    async def consume_once(
        self,
        *,
        stream: str,
        group: str,
        consumer_name: str,
        handler: Callable[[SwarmEvent], Awaitable[None]],
    ) -> int:
        """Consume a single batch of events from a stream."""

        await self._ensure_group(stream, group)
        records = await self._client.xreadgroup(
            groupname=group,
            consumername=consumer_name,
            streams={stream: ">"},
            count=self.settings.redis_consumer_batch_size,
            block=self.settings.redis_stream_block_ms,
        )
        processed = 0
        for stream_name, entries in records:
            for message_id, raw_fields in entries:
                decoded_fields = self._decode_fields(raw_fields)
                try:
                    event = self._deserialize_event(decoded_fields)
                except MessageDecodingError as exc:
                    await self.publish_to_dlq(
                        None,
                        error=str(exc),
                        stream=stream_name,
                        raw_message=decoded_fields,
                    )
                    await self.ack(stream_name, group, message_id)
                    processed += 1
                    continue

                await handler(event)
                await self.ack(stream_name, group, message_id)
                processed += 1
        return processed

    async def consume(
        self,
        *,
        stream: str,
        group: str,
        consumer_name: str,
        handler: Callable[[SwarmEvent], Awaitable[None]],
    ) -> None:
        """Continuously consume events from a stream."""

        while True:
            processed = await self.consume_once(
                stream=stream,
                group=group,
                consumer_name=consumer_name,
                handler=handler,
            )
            if processed == 0:
                await asyncio.sleep(self.settings.consumer_poll_delay_seconds)

    async def replay_from_dlq(self, event_id: str) -> None:
        """Replay support is deferred until DLQ indexing is implemented."""

        raise NotImplementedError(
            "Selective DLQ replay requires an index and is planned for a later sprint."
        )

    async def close(self) -> None:
        """Close the underlying Redis client."""

        await self._client.close()

    async def _ensure_group(self, stream: str, group: str) -> None:
        if (stream, group) in self._known_groups:
            return
        try:
            await self._client.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise MessageBusError(
                    f"Failed to create consumer group {group} for {stream}"
                ) from exc
        self._known_groups.add((stream, group))

    def _serialize_event(self, event: SwarmEvent) -> dict[str, str]:
        return {
            "id": event.id,
            "type": event.type.value,
            "task_id": event.task_id,
            "source_agent": event.source_agent,
            "target_agent": event.target_agent or "",
            "payload": json.dumps(event.payload),
            "metadata": json.dumps(event.metadata),
            "created_at": event.created_at.isoformat(),
            "parent_event_id": event.parent_event_id or "",
        }

    def _deserialize_event(self, fields: Mapping[str, str]) -> SwarmEvent:
        try:
            return SwarmEvent.model_validate(
                {
                    "id": fields["id"],
                    "type": fields["type"],
                    "task_id": fields["task_id"],
                    "source_agent": fields["source_agent"],
                    "target_agent": fields["target_agent"] or None,
                    "payload": json.loads(fields["payload"]),
                    "metadata": json.loads(fields["metadata"]),
                    "created_at": fields["created_at"],
                    "parent_event_id": fields["parent_event_id"] or None,
                }
            )
        except (KeyError, ValidationError, JSONDecodeError) as exc:
            raise MessageDecodingError(f"Unable to decode stream message: {exc}") from exc

    def _decode_fields(self, raw_fields: Mapping[bytes | str, bytes | str]) -> dict[str, str]:
        decoded: dict[str, str] = {}
        for key, value in raw_fields.items():
            decoded_key = key.decode("utf-8") if isinstance(key, bytes) else key
            decoded_value = value.decode("utf-8") if isinstance(value, bytes) else value
            decoded[decoded_key] = decoded_value
        return decoded
