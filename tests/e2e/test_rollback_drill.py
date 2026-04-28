"""Real-cluster drill that asserts FORGE auto-rollback within the SLO.

Scenario:
    1. Deploy a known-good Deployment to a real cluster.
    2. Roll out a deliberately broken image.
    3. Run :class:`RollbackController` against the live deployment with a
       fake metrics reader that always reports an unhealthy error rate.
    4. Assert rollback fires within 60 seconds and the audit log records it.

Gated by ``RUN_K8S_E2E=1`` so the regular test suite stays cluster-free.
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

from forge.agents.k8s_specialist.kubectl_client import (
    KubectlClient,
    SubprocessKubectlRunner,
)
from forge.agents.remediation.rollback_controller import RollbackController
from forge.core import audit
from forge.core.audit import AuditLog
from forge.core.config import Settings

pytestmark = pytest.mark.e2e


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


def test_bad_manifest_is_auto_rolled_back_within_sixty_seconds(tmp_path: Path) -> None:
    if os.environ.get("RUN_K8S_E2E") != "1":
        pytest.skip("RUN_K8S_E2E=1 not set; skipping live rollback drill")
    if not _cluster_reachable():
        pytest.skip("kubectl/cluster not reachable")

    audit.configure_default_audit_log(tmp_path / "audit.log")

    namespace = f"forge-rollback-{uuid.uuid4().hex[:8]}"
    deployment = "rollout-drill"

    create = _kubectl(["create", "namespace", namespace])
    assert create.returncode == 0, create.stderr

    try:
        v1 = tmp_path / "v1.yaml"
        v1.write_text(
            f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {deployment}
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
""",
            encoding="utf-8",
        )
        assert _kubectl(["apply", "-n", namespace, "-f", str(v1)]).returncode == 0
        assert (
            _kubectl(
                ["rollout", "status", f"deployment/{deployment}", "-n", namespace, "--timeout=60s"]
            ).returncode
            == 0
        )

        bad_image = "this-image-does-not-exist:nope"
        v_bad = tmp_path / "v_bad.yaml"
        v_bad.write_text(
            v1.read_text(encoding="utf-8").replace("nginx:1.25", bad_image),
            encoding="utf-8",
        )
        assert _kubectl(["apply", "-n", namespace, "-f", str(v_bad)]).returncode == 0

        settings = Settings(
            app_env="test",
            dry_run_mode=False,
            require_human_approval=False,
        )
        kubectl = KubectlClient(settings=settings, runner=SubprocessKubectlRunner())

        async def metrics_reader(ns: str, dep: str) -> float:
            del ns, dep
            return 0.99

        async def rollback_executor(ns: str, dep: str, rev: str) -> None:
            await kubectl.rollback_deployment(
                namespace=ns,
                deployment_name=dep,
                revision=rev,
                task_id="rollback-drill",
            )

        controller = RollbackController(
            metrics_reader=metrics_reader,
            rollback_executor=rollback_executor,
            observation_window_seconds=60,
            poll_interval_seconds=5,
        )

        started = time.monotonic()
        result = asyncio.run(
            controller.watch_and_rollback_if_needed(
                namespace=namespace,
                deployment_name=deployment,
                previous_revision="1",
                task_id="rollback-drill",
            )
        )
        elapsed = time.monotonic() - started

        assert result.rolled_back is True, result.reason
        assert elapsed <= 60.0, f"Rollback took {elapsed:.1f}s, exceeds 60s SLO"

        log = AuditLog(tmp_path / "audit.log")
        rollback_entries = [e for e in log.read_all() if e.action == "kubectl_rollback"]
        assert rollback_entries, "Audit log must record the rollback"
        assert rollback_entries[-1].task_id == "rollback-drill"

        history = _kubectl(
            ["rollout", "history", f"deployment/{deployment}", "-n", namespace]
        )
        assert history.returncode == 0
    finally:
        _kubectl(["delete", "namespace", namespace, "--ignore-not-found", "--wait=false"])
