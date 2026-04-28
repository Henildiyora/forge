from __future__ import annotations

import pytest

from forge.agents.base import BaseAgent
from forge.core.config import Settings
from forge.core.events import EventType, SwarmEvent
from forge.core.message_bus import InMemoryStreamClient, MessageBus


class _RecordingAgent(BaseAgent):
    agent_name = "recorder"

    def __init__(self, settings: Settings, bus: MessageBus) -> None:
        super().__init__(settings, bus)
        self.received: list[SwarmEvent] = []

    async def process_event(self, event: SwarmEvent) -> SwarmEvent | None:
        self.received.append(event)
        return None

    async def health_check(self) -> dict[str, object]:
        return self.default_health_status()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_in_memory_bus_round_trip(test_settings: Settings) -> None:
    bus = MessageBus.in_memory(test_settings)
    agent = _RecordingAgent(test_settings, bus)
    event = SwarmEvent(
        type=EventType.TASK_ASSIGNED,
        task_id="task-mem",
        source_agent="captain",
        target_agent=agent.agent_name,
        payload={"hello": "world"},
    )

    await bus.publish(event)
    processed = await bus.consume_once(
        stream=bus.stream_for(agent.agent_name),
        group=agent.agent_name,
        consumer_name="test-consumer",
        handler=agent.process_event,
    )

    assert processed == 1
    assert len(agent.received) == 1
    assert agent.received[0].task_id == "task-mem"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_in_memory_stream_length_helper(test_settings: Settings) -> None:
    client = InMemoryStreamClient()
    bus = MessageBus(settings=test_settings, stream_client=client)
    event = SwarmEvent(
        type=EventType.TASK_ASSIGNED,
        task_id="task-mem-len",
        source_agent="captain",
        target_agent="recorder",
        payload={},
    )
    await bus.publish(event)
    await bus.publish(event)

    assert client.stream_length(bus.stream_for("recorder")) == 2
