from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from forge.agents.k8s_specialist.kubectl_client import CommandResult
from forge.core.config import Settings
from forge.core.exceptions import SwarmError


class VClusterCommandError(SwarmError):
    """Raised when a vcluster command fails."""


@dataclass(frozen=True)
class SandboxCluster:
    """Ephemeral sandbox cluster metadata."""

    cluster_id: str
    namespace: str
    kubeconfig_path: str


class SupportsVClusterRunner(Protocol):
    """Protocol for injectable vcluster command execution."""

    async def run(self, args: list[str]) -> CommandResult: ...


class SubprocessVClusterRunner:
    """Default runner that shells out to the configured vcluster binary."""

    def __init__(self, binary_path: str) -> None:
        self.binary_path = binary_path

    async def run(self, args: list[str]) -> CommandResult:
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary_path,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise VClusterCommandError(
                "vcluster binary not found at "
                f"{self.binary_path}. Install vcluster first (macOS: "
                "`brew install loft-sh/tap/vcluster`) or choose the Docker "
                "Compose strategy if you only need Docker artifacts."
            ) from exc
        stdout_bytes, stderr_bytes = await process.communicate()
        return CommandResult(
            stdout=stdout_bytes.decode("utf-8"),
            stderr=stderr_bytes.decode("utf-8"),
            returncode=process.returncode or 0,
        )


class VClusterClient:
    """Lifecycle wrapper for ephemeral validation clusters."""

    def __init__(
        self,
        *,
        settings: Settings,
        runner: SupportsVClusterRunner | None = None,
    ) -> None:
        self.settings = settings
        self.runner = runner or SubprocessVClusterRunner(settings.vcluster_binary_path)

    async def create_sandbox(self, task_id: str) -> SandboxCluster:
        cluster_id = _cluster_id(task_id)
        namespace = f"{self.settings.k8s_namespace}-sandbox"
        kubeconfig_path = str(Path("/tmp") / f"{cluster_id}-kubeconfig.yaml")
        create_args = [
            "create",
            cluster_id,
            "--namespace",
            namespace,
            "--connect=false",
            "--update-current=false",
        ]
        connect_args = [
            "connect",
            cluster_id,
            "--namespace",
            namespace,
            "--update-current=false",
            "--kube-config",
            kubeconfig_path,
        ]
        await self._run(create_args)
        await self._run(connect_args)
        return SandboxCluster(
            cluster_id=cluster_id,
            namespace=namespace,
            kubeconfig_path=kubeconfig_path,
        )

    async def delete_sandbox(self, cluster: SandboxCluster) -> str:
        result = await self._run(
            [
                "delete",
                cluster.cluster_id,
                "--namespace",
                cluster.namespace,
            ]
        )
        return result.stdout.strip()

    async def _run(self, args: list[str]) -> CommandResult:
        result = await self.runner.run(args)
        if result.returncode != 0:
            raise VClusterCommandError(
                f"vcluster command failed ({' '.join(args)}): {result.stderr.strip()}"
            )
        return result


def _cluster_id(task_id: str) -> str:
    sanitized = "".join(character if character.isalnum() else "-" for character in task_id.lower())
    collapsed = "-".join(segment for segment in sanitized.split("-") if segment)
    suffix = collapsed[:18] if collapsed else "task"
    return f"sandbox-{suffix}"
