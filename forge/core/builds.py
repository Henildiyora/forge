from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from forge.agents.cicd_specialist.agent import CICDSpecialistAgent
from forge.agents.docker_specialist.agent import DockerSpecialistAgent
from forge.agents.k8s_specialist.agent import K8sSpecialistAgent
from forge.agents.k8s_specialist.kubectl_client import (
    KubectlClient,
    LiveExecutionContext,
)
from forge.agents.librarian.agent import LibrarianAgent
from forge.agents.librarian.ast_analyzer import CodebaseScanResult
from forge.agents.platform_specialist.generators import generate_existing_platform_overlay
from forge.agents.remediation.rollback_controller import RollbackController, RollbackResult
from forge.agents.sandbox_tester.agent import SandboxTesterAgent, SandboxValidationResult
from forge.agents.sandbox_tester.vcluster_client import VClusterCommandError
from forge.agents.serverless_specialist.generators import generate_serverless_assets
from forge.core.approvals import ApprovalRequest, approval_store
from forge.core.checkpoints import CheckpointRecord, CheckpointStore
from forge.core.config import Settings
from forge.core.exceptions import SandboxToolingError
from forge.core.message_bus import MessageBus
from forge.core.strategies import DeploymentStrategy
from forge.core.workspace import ArtifactManifest, ForgeWorkspace


class GeneratedArtifacts(BaseModel):
    """Artifacts produced for a specific deployment strategy."""

    task_id: str = Field(description="Build task identifier.")
    strategy: DeploymentStrategy = Field(description="Selected deployment strategy.")
    dockerfile: str | None = Field(default=None)
    docker_compose: str | None = Field(default=None)
    k8s_manifests: dict[str, str] = Field(default_factory=dict)
    cicd_pipeline: str | None = Field(default=None)
    supplemental_files: dict[str, str] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class BuildExecutionResult(BaseModel):
    """Overall result of a `forge build` execution."""

    decision_reasoning: str = Field(description="User-facing decision reasoning.")
    generated: GeneratedArtifacts = Field(description="Generated artifacts bundle.")
    sandbox_validation: SandboxValidationResult | None = Field(default=None)
    approval_request: ApprovalRequest | None = Field(default=None)
    approval_url: str | None = Field(default=None)
    live_ready: bool = Field(
        default=False,
        description=(
            "Whether sandbox validation succeeded and the build is eligible "
            "for live approval."
        ),
    )
    rollback_result: RollbackResult | None = Field(default=None)


async def index_project(
    *,
    project_path: str | Path,
    settings: Settings,
    workspace: ForgeWorkspace,
    librarian: LibrarianAgent,
) -> CodebaseScanResult:
    """Run the Librarian scan and persist the result into `.forge/index.json`."""

    result = await librarian.analyze_codebase(str(Path(project_path).expanduser().resolve()))
    workspace.save_index(result)
    return CodebaseScanResult.model_validate(result.model_dump(mode="json"))


async def generate_strategy_artifacts(
    *,
    settings: Settings,
    project_path: str | Path,
    strategy: DeploymentStrategy,
    cloud: str | None,
    message_bus: MessageBus,
) -> GeneratedArtifacts:
    """Generate deployment artifacts for the selected strategy."""

    task_id = f"build-{uuid4().hex[:8]}"
    librarian = LibrarianAgent(settings=settings, message_bus=message_bus)
    scan_result = await librarian.analyze_codebase(str(Path(project_path).expanduser().resolve()))
    docker = DockerSpecialistAgent(settings=settings, message_bus=message_bus)
    k8s = K8sSpecialistAgent(settings=settings, message_bus=message_bus)
    cicd = CICDSpecialistAgent(settings=settings, message_bus=message_bus)

    if strategy == DeploymentStrategy.DOCKER_COMPOSE:
        bundle = await docker.generate_artifacts(scan_result)
        return GeneratedArtifacts(
            task_id=task_id,
            strategy=strategy,
            dockerfile=bundle.dockerfile,
            docker_compose=bundle.docker_compose,
            evidence=bundle.evidence,
            confidence=bundle.confidence,
        )
    if strategy == DeploymentStrategy.KUBERNETES:
        docker_bundle = await docker.generate_artifacts(scan_result)
        k8s_bundle = await k8s.generate_artifacts(scan_result)
        cicd_bundle = await cicd.generate_artifacts(scan_result)
        return GeneratedArtifacts(
            task_id=task_id,
            strategy=strategy,
            dockerfile=docker_bundle.dockerfile,
            docker_compose=docker_bundle.docker_compose,
            k8s_manifests=k8s_bundle.manifests,
            cicd_pipeline=cicd_bundle.pipeline,
            evidence=docker_bundle.evidence + k8s_bundle.evidence + cicd_bundle.evidence,
            confidence=min(docker_bundle.confidence, k8s_bundle.confidence, cicd_bundle.confidence),
        )
    if strategy == DeploymentStrategy.CICD_ONLY:
        cicd_bundle = await cicd.generate_artifacts(scan_result)
        return GeneratedArtifacts(
            task_id=task_id,
            strategy=strategy,
            cicd_pipeline=cicd_bundle.pipeline,
            evidence=cicd_bundle.evidence,
            confidence=cicd_bundle.confidence,
        )
    if strategy == DeploymentStrategy.SERVERLESS:
        chosen_cloud = "aws" if cloud not in {"aws", "gcp"} else cloud
        serverless_bundle = generate_serverless_assets(scan_result, cloud=chosen_cloud)
        return GeneratedArtifacts(
            task_id=task_id,
            strategy=strategy,
            supplemental_files=serverless_bundle.files,
            evidence=serverless_bundle.evidence,
            confidence=serverless_bundle.confidence,
        )
    overlay_bundle = generate_existing_platform_overlay(scan_result)
    cicd_bundle = await cicd.generate_artifacts(scan_result)
    return GeneratedArtifacts(
        task_id=task_id,
        strategy=strategy,
        cicd_pipeline=cicd_bundle.pipeline,
        supplemental_files=overlay_bundle.files,
        evidence=overlay_bundle.evidence + cicd_bundle.evidence,
        confidence=min(overlay_bundle.confidence, cicd_bundle.confidence),
    )


def write_generated_artifacts(
    *,
    output_dir: str | Path,
    generated: GeneratedArtifacts,
    workspace: ForgeWorkspace,
) -> list[str]:
    """Write generated artifacts to disk and persist an artifact manifest."""

    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    if generated.dockerfile is not None:
        dockerfile = output_root / "Dockerfile"
        dockerfile.write_text(generated.dockerfile, encoding="utf-8")
        written.append(dockerfile.relative_to(output_root).as_posix())
    if generated.docker_compose is not None:
        compose = output_root / "docker-compose.generated.yml"
        compose.write_text(generated.docker_compose, encoding="utf-8")
        written.append(compose.relative_to(output_root).as_posix())
    for name, content in generated.k8s_manifests.items():
        manifest_path = output_root / name
        manifest_path.write_text(content, encoding="utf-8")
        written.append(manifest_path.relative_to(output_root).as_posix())
    if generated.cicd_pipeline is not None:
        workflow_path = output_root / ".github" / "workflows" / "generated-ci.yml"
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(generated.cicd_pipeline, encoding="utf-8")
        written.append(workflow_path.relative_to(output_root).as_posix())
    for name, content in generated.supplemental_files.items():
        extra_path = output_root / name
        extra_path.parent.mkdir(parents=True, exist_ok=True)
        extra_path.write_text(content, encoding="utf-8")
        written.append(extra_path.relative_to(output_root).as_posix())
    deploy_guide_path = output_root / "instruction_deploy.md"
    deploy_guide_path.write_text(
        _render_deploy_instruction(
            strategy=generated.strategy,
            files=written,
            output_root=output_root,
        ),
        encoding="utf-8",
    )
    written.append(deploy_guide_path.relative_to(output_root).as_posix())
    workspace.save_artifacts(
        ArtifactManifest(
            task_id=generated.task_id,
            strategy=generated.strategy.value,
            files=written,
        )
    )
    return written


def _render_deploy_instruction(
    *,
    strategy: DeploymentStrategy,
    files: list[str],
    output_root: Path,
) -> str:
    file_lines = "\n".join(
        f"- `{name}`: {_describe_generated_file(name)}" for name in sorted(files)
    )
    commands = _strategy_commands(strategy)
    command_block = "\n".join(commands)
    improvement_notes = _strategy_improvements(strategy)
    return (
        "# Deployment Instructions\n\n"
        f"Generated output directory: `{output_root}`\n\n"
        "## What FORGE Generated\n"
        f"{file_lines}\n\n"
        "## Step-by-Step Commands\n"
        "Run these commands from your project root unless noted otherwise.\n\n"
        "```bash\n"
        f"{command_block}\n"
        "```\n\n"
        "## Validation Checklist\n"
        "- Confirm application starts without errors.\n"
        "- Confirm health endpoint responds successfully.\n"
        "- Confirm logs show no repeated crash/restart loops.\n"
        "- Confirm environment variables/secrets are populated for your runtime.\n\n"
        "## If Something Fails\n"
        "- Re-open generated files and verify image names, ports, and env vars.\n"
        "- Validate YAML syntax before applying changes.\n"
        "- Run `forge doctor` to check local prerequisites.\n\n"
        "## Next Improvements\n"
        f"{improvement_notes}\n"
    )


def _describe_generated_file(path: str) -> str:
    if path == "Dockerfile":
        return "Container build recipe for your app image."
    if path.endswith("docker-compose.generated.yml"):
        return "Local or single-host orchestration for one or more containers."
    if path.endswith(".yaml") and "workflow" in path:
        return "Generated CI pipeline workflow."
    if path.endswith(".yaml") or path.endswith(".yml"):
        return "Deployment manifest used by the selected platform."
    if path == "instruction_deploy.md":
        return "Step-by-step runbook describing how to deploy and verify."
    return "Supporting deployment artifact generated by FORGE."


def _strategy_commands(strategy: DeploymentStrategy) -> list[str]:
    if strategy == DeploymentStrategy.DOCKER_COMPOSE:
        return [
            "cd .forge/generated",
            "docker compose -f docker-compose.generated.yml up --build -d",
            "docker compose -f docker-compose.generated.yml ps",
            "docker compose -f docker-compose.generated.yml logs --tail=100",
        ]
    if strategy == DeploymentStrategy.KUBERNETES:
        return [
            "cd .forge/generated",
            "docker build -t your-image:latest -f Dockerfile ..",
            "kubectl apply -f deployment.yaml",
            "kubectl apply -f service.yaml",
            "kubectl get pods,svc",
            "kubectl logs deployment/app --tail=100",
        ]
    if strategy == DeploymentStrategy.SERVERLESS:
        return [
            "cd .forge/generated",
            "# Install your serverless runtime tooling if needed",
            "serverless deploy",
            "serverless info",
        ]
    if strategy == DeploymentStrategy.CICD_ONLY:
        return [
            "cd .forge/generated",
            "ls .github/workflows",
            "# Copy the generated workflow into your repository's .github/workflows/",
            "git add .github/workflows/generated-ci.yml",
            "git commit -m \"Add generated CI workflow\"",
        ]
    return [
        "cd .forge/generated",
        "# Review supplemental overlay files generated by FORGE",
        "ls",
    ]


def _strategy_improvements(strategy: DeploymentStrategy) -> str:
    if strategy == DeploymentStrategy.DOCKER_COMPOSE:
        return (
            "- Add CI to build and test images automatically.\n"
            "- Add observability (metrics + centralized logs).\n"
            "- Migrate to Kubernetes when you need autoscaling or multi-service resilience."
        )
    if strategy == DeploymentStrategy.KUBERNETES:
        return (
            "- Add Horizontal Pod Autoscaler and resource limits.\n"
            "- Add progressive delivery (canary/blue-green) when risk tolerance requires it.\n"
            "- Add centralized monitoring and alerting for SLO-driven operations."
        )
    if strategy == DeploymentStrategy.SERVERLESS:
        return (
            "- Add explicit cold-start and latency monitoring.\n"
            "- Add staged environments (dev/stage/prod) with separate configs.\n"
            "- Add CI quality gates before deployment."
        )
    if strategy == DeploymentStrategy.CICD_ONLY:
        return (
            "- Add environment promotion steps (dev to stage to prod).\n"
            "- Add security scanning (dependencies, images, IaC).\n"
            "- Add deployment automation once target platform is finalized."
        )
    return (
        "- Validate generated overlays in a non-production environment first.\n"
        "- Add CI checks for generated config drift.\n"
        "- Add observability before scaling traffic."
    )


async def validate_kubernetes_build(
    *,
    settings: Settings,
    project_path: str | Path,
    generated: GeneratedArtifacts,
    message_bus: MessageBus,
) -> SandboxValidationResult | None:
    """Validate Kubernetes manifests in the sandbox when the strategy uses Kubernetes."""

    if generated.strategy != DeploymentStrategy.KUBERNETES or not generated.k8s_manifests:
        return None
    scan_result = await LibrarianAgent(settings=settings, message_bus=message_bus).analyze_codebase(
        str(Path(project_path).expanduser().resolve())
    )
    sandbox = SandboxTesterAgent(settings=settings, message_bus=message_bus)
    try:
        return await sandbox.validate_sandbox(
            task_id=generated.task_id,
            manifests=generated.k8s_manifests,
            namespace=settings.k8s_namespace,
            expected_port=scan_result.port,
        )
    except VClusterCommandError as exc:
        raise SandboxToolingError(str(exc)) from exc


async def request_build_approval(
    *,
    generated: GeneratedArtifacts,
    checkpoint_store: CheckpointStore,
    approval_summary: str,
    approval_url: str,
) -> ApprovalRequest:
    """Create an approval request and persist a checkpoint for later resume."""

    request = approval_store.create_request(
        task_id=generated.task_id,
        workflow_type="build",
        severity="medium",
        summary=approval_summary,
        reason=(
            "Sandbox validation passed and FORGE is waiting for human "
            "approval before live deployment."
        ),
        proposed_action="Apply generated manifests to the live Kubernetes cluster.",
        evidence=generated.evidence[-6:],
    )
    await checkpoint_store.save(
        CheckpointRecord(
            task_id=generated.task_id,
            workflow_type="build",
            current_step="awaiting_approval",
            approval_request_id=request.id,
            state={
                "task_id": generated.task_id,
                "strategy": generated.strategy.value,
                "k8s_manifests": generated.k8s_manifests,
                "approval_url": approval_url,
                "namespace": "default",
            },
        )
    )
    return request


async def resume_live_build(
    *,
    settings: Settings,
    checkpoint_store: CheckpointStore,
    task_id: str,
    approved_by: str,
) -> BuildExecutionResult | None:
    """Resume a paused live deployment after approval."""

    checkpoint = await checkpoint_store.load(task_id)
    if checkpoint is None:
        return None
    strategy_value = checkpoint.state.get("strategy", DeploymentStrategy.KUBERNETES.value)
    strategy = DeploymentStrategy(str(strategy_value))
    if strategy != DeploymentStrategy.KUBERNETES:
        await checkpoint_store.delete(task_id)
        return None
    raw_manifests = checkpoint.state.get("k8s_manifests", {})
    if not isinstance(raw_manifests, dict):
        await checkpoint_store.delete(task_id)
        return None
    manifest_map = {
        name: content
        for name, content in raw_manifests.items()
        if isinstance(name, str) and isinstance(content, str)
    }
    kubectl = KubectlClient(settings=settings.model_copy(update={"dry_run_mode": False}))
    for manifest in manifest_map.values():
        await kubectl.dry_run_manifest(manifest, task_id=task_id)
    await kubectl.apply_manifests_live(
        manifest_map,
        context=LiveExecutionContext(
            sandbox_test_passed=True,
            approval_status="approved",
            task_id=task_id,
            dry_run_passed=True,
        ),
        namespace=str(checkpoint.state.get("namespace", settings.k8s_namespace)),
        approved_by=approved_by,
    )

    async def read_error_rate(namespace: str, deployment_name: str) -> float:
        del namespace, deployment_name
        return 0.0

    async def rollback(namespace: str, deployment_name: str, revision: str) -> None:
        await kubectl.rollback_deployment(
            namespace=namespace,
            deployment_name=deployment_name,
            revision=revision,
            task_id=task_id,
        )

    rollback_result = await RollbackController(
        metrics_reader=read_error_rate,
        rollback_executor=rollback,
    ).watch_and_rollback_if_needed(
        namespace=str(checkpoint.state.get("namespace", settings.k8s_namespace)),
        deployment_name=_deployment_name_from_manifests(manifest_map) or "app",
        previous_revision="1",
        task_id=task_id,
    )
    await checkpoint_store.delete(task_id)
    return BuildExecutionResult(
        decision_reasoning="Live deployment resumed from approval checkpoint.",
        generated=GeneratedArtifacts(
            task_id=task_id,
            strategy=strategy,
            k8s_manifests=manifest_map,
            evidence=["Resumed Kubernetes deployment from an approval checkpoint."],
            confidence=0.9,
        ),
        live_ready=True,
        rollback_result=rollback_result,
    )


def _deployment_name_from_manifests(manifests: dict[str, str]) -> str | None:
    deployment_yaml = manifests.get("deployment.yaml")
    if deployment_yaml is None:
        return None
    for line in deployment_yaml.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.split(":", maxsplit=1)[1].strip()
    return None

