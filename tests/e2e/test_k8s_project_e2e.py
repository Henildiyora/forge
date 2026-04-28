"""End-to-end test of the Kubernetes strategy path against a real cluster.

Gated by ``RUN_K8S_E2E=1`` because it requires a usable kubectl context
(Kind, k3d, minikube, or any cluster the developer is happy to deploy into).
The test creates a unique throwaway namespace, applies the FORGE-generated
manifests via ``kubectl apply``, waits for the deployment to become Ready,
and then deletes the namespace to clean up.

Note: This test does NOT use the live-execution gate path because that path
requires sandbox validation + an approved checkpoint. We exercise that
contract separately in the live-gate integration tests.
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
import yaml

from forge.agents.librarian.agent import LibrarianAgent
from forge.cli.runtime import local_message_bus
from forge.conversation.engine import ConversationEngine
from forge.core.builds import (
    generate_strategy_artifacts,
    write_generated_artifacts,
)
from forge.core.config import Settings
from forge.core.llm import LLMClient
from forge.core.strategies import DeploymentStrategy
from forge.core.workspace import ForgeWorkspace

pytestmark = pytest.mark.e2e


def _kubectl_available() -> bool:
    if shutil.which("kubectl") is None:
        return False
    try:
        result = subprocess.run(
            ["kubectl", "cluster-info", "--request-timeout=2s"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _kubectl(args: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _wait_for_deployment_ready(namespace: str, deployment: str, timeout: float = 180.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        proc = _kubectl(
            [
                "rollout",
                "status",
                f"deployment/{deployment}",
                "-n",
                namespace,
                "--timeout=10s",
            ],
            timeout=15,
        )
        if proc.returncode == 0 and "successfully rolled out" in proc.stdout:
            return True
        time.sleep(2.0)
    return False


def test_kubernetes_strategy_deploys_to_real_cluster(
    tmp_path: Path,
    python_fastapi_project: Path,
) -> None:
    if os.environ.get("RUN_K8S_E2E") != "1":
        pytest.skip("RUN_K8S_E2E=1 not set; skipping live cluster test")
    if not _kubectl_available():
        pytest.skip("kubectl/cluster not reachable")

    workdir = tmp_path / "project"
    shutil.copytree(python_fastapi_project, workdir)
    shutil.rmtree(workdir / ".forge", ignore_errors=True)

    settings = Settings(app_env="test", llm_backend="heuristic")
    bus = local_message_bus(settings)
    workspace = ForgeWorkspace(workdir, settings)
    librarian = LibrarianAgent(settings=settings, message_bus=bus)

    scan = asyncio.run(librarian.analyze_codebase(str(workdir)))

    llm = LLMClient(settings)
    engine = ConversationEngine(llm=llm, scan_result=scan)
    intent = asyncio.run(
        engine.interpret_intent(
            "I have multiple services and need autoscaling, deploy with Kubernetes"
        )
    )
    intent.mentioned_scale = "medium"

    generated = asyncio.run(
        generate_strategy_artifacts(
            settings=settings,
            project_path=workdir,
            strategy=DeploymentStrategy.KUBERNETES,
            cloud=None,
            message_bus=bus,
        )
    )
    assert generated.k8s_manifests, "K8s strategy should produce manifests"
    artifact_dir = workdir / ".forge" / "generated"
    written = write_generated_artifacts(
        output_dir=artifact_dir,
        generated=generated,
        workspace=workspace,
    )
    assert any(name.endswith(".yaml") for name in written)

    namespace = f"forge-e2e-{uuid.uuid4().hex[:8]}"
    create_ns = _kubectl(["create", "namespace", namespace])
    assert create_ns.returncode == 0, create_ns.stderr

    deployment_name: str | None = None
    try:
        for manifest_name, content in generated.k8s_manifests.items():
            parsed = yaml.safe_load(content)
            if isinstance(parsed, dict) and parsed.get("kind") == "Deployment":
                deployment_name = parsed.get("metadata", {}).get("name")
            apply = subprocess.run(
                ["kubectl", "apply", "-n", namespace, "-f", "-"],
                input=content,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            assert apply.returncode == 0, (
                f"kubectl apply for {manifest_name} failed: {apply.stderr}"
            )

        assert deployment_name is not None, "Generated manifests did not include a Deployment"
        ready = _wait_for_deployment_ready(namespace, deployment_name, timeout=180.0)
        assert ready, f"Deployment {deployment_name} never became Ready"

        pods = _kubectl(["get", "pods", "-n", namespace, "-o", "name"])
        assert pods.returncode == 0
        assert pods.stdout.strip(), "No pods found after deployment"
    finally:
        _kubectl(["delete", "namespace", namespace, "--ignore-not-found", "--wait=false"])
