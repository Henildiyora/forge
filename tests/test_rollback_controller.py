from __future__ import annotations

import pytest

from forge.agents.remediation.rollback_controller import RollbackController


@pytest.mark.asyncio
async def test_rollback_controller_keeps_healthy_deployments() -> None:
    samples = iter([0.01, 0.02, 0.03])
    rolled_back: list[tuple[str, str, str]] = []

    async def metrics_reader(namespace: str, deployment_name: str) -> float:
        del namespace, deployment_name
        return next(samples, 0.03)

    async def rollback_executor(namespace: str, deployment_name: str, revision: str) -> None:
        rolled_back.append((namespace, deployment_name, revision))

    result = await RollbackController(
        metrics_reader=metrics_reader,
        rollback_executor=rollback_executor,
        observation_window_seconds=15,
        poll_interval_seconds=5,
    ).watch_and_rollback_if_needed(
        namespace="default",
        deployment_name="api",
        previous_revision="1",
        task_id="deploy-1",
    )

    assert result.rolled_back is False
    assert rolled_back == []


@pytest.mark.asyncio
async def test_rollback_controller_triggers_on_regression() -> None:
    samples = iter([0.01, 0.08])
    rolled_back: list[tuple[str, str, str]] = []

    async def metrics_reader(namespace: str, deployment_name: str) -> float:
        del namespace, deployment_name
        return next(samples, 0.08)

    async def rollback_executor(namespace: str, deployment_name: str, revision: str) -> None:
        rolled_back.append((namespace, deployment_name, revision))

    result = await RollbackController(
        metrics_reader=metrics_reader,
        rollback_executor=rollback_executor,
        observation_window_seconds=15,
        poll_interval_seconds=5,
    ).watch_and_rollback_if_needed(
        namespace="default",
        deployment_name="api",
        previous_revision="2",
        task_id="deploy-2",
    )

    assert result.rolled_back is True
    assert rolled_back == [("default", "api", "2")]
