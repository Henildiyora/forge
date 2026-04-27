from __future__ import annotations

import pytest

from forge.agents.base import BaseAgent
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import MessageBus
from tests.conftest import FakeRedisStreamClient


class EchoAgent(BaseAgent):
    agent_name = "echo"

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        return SwarmEvent(
            type=EventType.TASK_COMPLETED,
            task_id=event.task_id,
            source_agent=self.agent_name,
            target_agent="captain",
            payload={"received": event.payload},
            parent_event_id=event.id,
        )

    async def health_check(self) -> dict[str, object]:
        return self.default_health_status()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_consume_and_respond(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> None:
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    agent = EchoAgent(test_settings, bus)
    event = SwarmEvent(
        type=EventType.TASK_ASSIGNED,
        task_id="task-123",
        source_agent="captain",
        target_agent="echo",
        payload={"intent": "deploy"},
    )

    await bus.publish(event)
    processed = await bus.consume_once(
        stream=bus.stream_for("echo"),
        group="echo",
        consumer_name="test-consumer",
        handler=agent._handle_event,
    )

    assert processed == 1
    assert fake_stream_client.streams[bus.stream_for("captain")]
    _, raw_response = fake_stream_client.streams[bus.stream_for("captain")][0]
    response = bus._deserialize_event(raw_response)
    assert response.type == EventType.TASK_COMPLETED
    assert response.payload == {"received": {"intent": "deploy"}}
    assert response.parent_event_id == event.id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_malformed_messages_are_sent_to_dlq(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
) -> None:
    bus = MessageBus(settings=test_settings, stream_client=fake_stream_client)
    await fake_stream_client.xadd(
        bus.stream_for("echo"),
        {
            "id": "broken",
            "type": "task.assigned",
            "task_id": "task-999",
            "source_agent": "captain",
            "target_agent": "echo",
            "payload": "{not-json}",
            "metadata": "{}",
            "created_at": "2026-01-01T00:00:00+00:00",
            "parent_event_id": "",
        },
    )

    processed = await bus.consume_once(
        stream=bus.stream_for("echo"),
        group="echo",
        consumer_name="test-consumer",
        handler=lambda _: pytest.fail("Malformed events must never reach handlers"),
    )

    assert processed == 1
    assert fake_stream_client.streams[test_settings.dead_letter_stream]
