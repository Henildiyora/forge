"""End-to-end test of the incident remediation loop.

Two scenarios:

1. ``test_incident_remediation_full_loop_in_memory``: exercises the full
   evidence -> hypothesis -> fix -> evaluation -> rollback loop with a fake
   metrics reader so no cluster is required. Always runs in CI.

2. ``test_rollback_controller_against_real_cluster`` (gated by
   ``RUN_K8S_E2E=1``): drills the rollback controller against a real
   deployment so we know the kubectl rollback wiring works in production.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from forge.agents.remediation.agent import RemediationAgent
from forge.agents.remediation.fix_evaluator import FixEvaluator
from forge.agents.remediation.rollback_controller import RollbackController
from forge.cli.runtime import local_message_bus
from forge.core.config import Settings

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_incident_remediation_full_loop_in_memory() -> None:
    settings = Settings(app_env="test", llm_backend="heuristic")
    bus = local_message_bus(settings)
    agent = RemediationAgent(settings=settings, message_bus=bus, evaluator=FixEvaluator())

    alert_data: dict[str, object] = {
        "service": "payments",
        "namespace": "default",
        "deployment_name": "payments-api",
        "previous_revision": "42",
        "error_rate": 0.18,
        "latency_p95_ms": 920.0,
        "restart_count": 3.0,
        "error_log_count": 12,
        "anomalies": ["spike-5xx", "elevated-latency"],
        "recent_change_detected": True,
    }

    evidence = await agent.collect_evidence(alert_data=alert_data)
    assert len(evidence) >= 3

    hypothesis = await agent.hypothesize_root_cause(alert_data=alert_data, evidence=evidence)
    assert hypothesis.confidence >= 0.70, (
        f"Confidence {hypothesis.confidence} below the 0.70 hallucination guard."
    )
    assert hypothesis.evidence, "Hypothesis must cite the evidence it depends on"

    proposal = await agent.propose_fix(alert_data=alert_data, hypothesis=hypothesis)
    assert proposal.strategy == "rollback"
    assert proposal.requires_human_approval is True
    assert proposal.evidence, "Fix proposals must carry their supporting evidence"

    evaluation = await agent.evaluate_fix(proposal)
    assert evaluation.safe_for_sandbox is True
    assert evaluation.requires_human_approval is True
    assert evaluation.score >= 0.7

    rollback_calls: list[tuple[str, str, str]] = []
    error_rate_samples = iter([0.12, 0.15, 0.0, 0.0])

    async def fake_metrics_reader(namespace: str, deployment: str) -> float:
        del namespace, deployment
        try:
            return next(error_rate_samples)
        except StopIteration:
            return 0.0

    async def fake_rollback(namespace: str, deployment: str, revision: str) -> None:
        rollback_calls.append((namespace, deployment, revision))

    controller = RollbackController(
        metrics_reader=fake_metrics_reader,
        rollback_executor=fake_rollback,
        observation_window_seconds=20,
        poll_interval_seconds=5,
    )
    deployment_name = proposal.deployment_name or "payments-api"
    previous_revision = proposal.previous_revision or "1"
    result = await controller.watch_and_rollback_if_needed(
        namespace="default",
        deployment_name=deployment_name,
        previous_revision=previous_revision,
        task_id="incident-e2e",
    )
    assert result.rolled_back is True
    assert rollback_calls == [("default", deployment_name, previous_revision)]
    assert result.observed_error_rates[0] == pytest.approx(0.12)


def _kubectl(
    args: list[str],
    timeout: float = 60.0,
    **kwargs: object,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        **kwargs,  # type: ignore[arg-type]
    )


def _cluster_reachable() -> bool:
    if shutil.which("kubectl") is None:
        return False
    try:
        proc = subprocess.run(
            ["kubectl", "cluster-info", "--request-timeout=2s"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def test_rollback_controller_against_real_cluster(tmp_path: Path) -> None:
    if os.environ.get("RUN_K8S_E2E") != "1":
        pytest.skip("RUN_K8S_E2E=1 not set; skipping live cluster rollback drill")
    if not _cluster_reachable():
        pytest.skip("kubectl/cluster not reachable")

    namespace = f"forge-roll-{uuid.uuid4().hex[:8]}"
    deployment = "rollout-canary"

    create = _kubectl(["create", "namespace", namespace])
    assert create.returncode == 0, create.stderr

    try:
        v1 = (tmp_path / "v1.yaml")
        v1.write_text(
            f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {deployment}
  labels:
    app: {deployment}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {deployment}
  template:
    metadata:
      labels:
        app: {deployment}
    spec:
      containers:
      - name: app
        image: nginx:1.25
        ports:
        - containerPort: 80
""",
            encoding="utf-8",
        )
        apply_v1 = _kubectl(["apply", "-n", namespace, "-f", str(v1)])
        assert apply_v1.returncode == 0, apply_v1.stderr

        rollout = _kubectl(
            ["rollout", "status", f"deployment/{deployment}", "-n", namespace, "--timeout=60s"]
        )
        assert rollout.returncode == 0, rollout.stdout + rollout.stderr

        v2 = tmp_path / "v2.yaml"
        v2.write_text(
            v1.read_text(encoding="utf-8").replace("nginx:1.25", "nginx:1.26"),
            encoding="utf-8",
        )
        apply_v2 = _kubectl(["apply", "-n", namespace, "-f", str(v2)])
        assert apply_v2.returncode == 0, apply_v2.stderr

        rollout_calls: list[tuple[str, str, str]] = []

        async def metrics() -> float:
            return 0.99

        async def rollback(ns: str, dep: str, rev: str) -> None:
            rollout_calls.append((ns, dep, rev))
            proc = _kubectl(["rollout", "undo", f"deployment/{dep}", "-n", ns])
            assert proc.returncode == 0, proc.stderr

        async def metrics_reader(ns: str, dep: str) -> float:
            del ns, dep
            return await metrics()

        controller = RollbackController(
            metrics_reader=metrics_reader,
            rollback_executor=rollback,
            observation_window_seconds=10,
            poll_interval_seconds=5,
        )
        result = asyncio.run(
            controller.watch_and_rollback_if_needed(
                namespace=namespace,
                deployment_name=deployment,
                previous_revision="1",
                task_id="rollback-drill",
            )
        )
        assert result.rolled_back is True
        assert rollout_calls, "Rollback executor was never invoked"

        time.sleep(3.0)
        history = _kubectl(["rollout", "history", f"deployment/{deployment}", "-n", namespace])
        assert history.returncode == 0, history.stderr
    finally:
        _kubectl(["delete", "namespace", namespace, "--ignore-not-found", "--wait=false"])
