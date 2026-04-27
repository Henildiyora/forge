from __future__ import annotations

from pathlib import Path

import pytest

from forge.core.approvals import approval_store
from forge.core.config import Settings
from forge.core.hardening import hardening_store, run_hardening_suite
from forge.core.observability import observability_store
from tests.conftest import FakeRedisStreamClient


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hardening_suite_runs_all_scenarios_and_restores_runtime_state(
    test_settings: Settings,
    fake_stream_client: FakeRedisStreamClient,
    python_fastapi_project: Path,
) -> None:
    approval_store.reset()
    observability_store.reset()
    hardening_store.reset()
    baseline_request = approval_store.create_request(
        task_id="baseline-incident",
        workflow_type="incident",
        severity="high",
        summary="Baseline approval",
        reason="Preserve existing state",
        proposed_action="Do nothing",
        evidence=["Baseline request should survive the suite."],
    )

    report = await run_hardening_suite(
        settings=test_settings,
        project_path=python_fastapi_project,
        max_iterations=3,
        stream_client=fake_stream_client,
    )

    assert report.total_scenarios == 5
    assert report.failed_scenarios == 0
    assert report.readiness_score == 1.0
    assert report.observability.total_runs >= 4
    assert approval_store.list_requests(status="pending")[0].id == baseline_request.id
    assert observability_store.summary().total_runs == 0
    latest = hardening_store.latest()
    assert latest is not None
    assert latest.total_scenarios == 5
