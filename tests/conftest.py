from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

import pytest

from swarm.core.config import Settings


class FakeRedisStreamClient:
    """Minimal async Redis Streams test double."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = defaultdict(list)
        self.groups: dict[tuple[str, str], int] = {}
        self.acked: list[tuple[str, str, str]] = []

    async def xadd(
        self,
        name: str,
        fields: Mapping[str, str],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        message_id = f"{len(self.streams[name]) + 1}-0"
        self.streams[name].append((message_id, dict(fields)))
        if maxlen is not None and len(self.streams[name]) > maxlen:
            self.streams[name] = self.streams[name][-maxlen:]
        return message_id

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> object:
        if (name, groupname) in self.groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        if mkstream and name not in self.streams:
            self.streams[name] = []
        self.groups[(name, groupname)] = 0
        return True

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        count: int = 1,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, Mapping[bytes | str, bytes | str]]]]]:
        del consumername, block
        responses: list[tuple[str, list[tuple[str, Mapping[bytes | str, bytes | str]]]]] = []
        for stream_name in streams:
            index = self.groups.get((stream_name, groupname), 0)
            entries = self.streams.get(stream_name, [])[index : index + count]
            if not entries:
                continue
            self.groups[(stream_name, groupname)] = index + len(entries)
            responses.append(
                (
                    stream_name,
                    [
                        (
                            message_id,
                            {
                                key.encode("utf-8"): value.encode("utf-8")
                                for key, value in values.items()
                            },
                        )
                        for message_id, values in entries
                    ],
                )
            )
        return responses

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        for message_id in ids:
            self.acked.append((name, groupname, message_id))
        return len(ids)

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_stream_client() -> FakeRedisStreamClient:
    return FakeRedisStreamClient()


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_env="test",
        redis_url="redis://test",
        redis_stream_block_ms=0,
        consumer_poll_delay_seconds=0.0,
    )


@pytest.fixture
def sample_projects_root() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "sample_projects"


@pytest.fixture
def python_fastapi_project(sample_projects_root: Path) -> Path:
    return sample_projects_root / "python_fastapi"


@pytest.fixture
def node_express_project(sample_projects_root: Path) -> Path:
    return sample_projects_root / "node_express"


@pytest.fixture
def go_service_project(sample_projects_root: Path) -> Path:
    return sample_projects_root / "go_service"
