from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol, cast

from swarm.core.config import Settings
from swarm.core.exceptions import ConfigurationError, SwarmError


class KubectlCommandError(SwarmError):
    """Raised when a kubectl invocation fails."""


@dataclass(frozen=True)
class CommandResult:
    """Normalized output from a kubectl command invocation."""

    stdout: str
    stderr: str
    returncode: int


class SupportsKubectlRunner(Protocol):
    """Protocol for injectable kubectl command execution."""

    async def run(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> CommandResult: ...


class SubprocessKubectlRunner:
    """Default runner that shells out to the local kubectl binary."""

    async def run(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            "kubectl",
            *args,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate(
            input_text.encode("utf-8") if input_text is not None else None
        )
        return CommandResult(
            stdout=stdout_bytes.decode("utf-8"),
            stderr=stderr_bytes.decode("utf-8"),
            returncode=process.returncode or 0,
        )


class KubectlClient:
    """Safe wrapper around common kubectl operations used by the swarm."""

    def __init__(
        self,
        *,
        settings: Settings,
        runner: SupportsKubectlRunner | None = None,
    ) -> None:
        self.settings = settings
        self.runner = runner or SubprocessKubectlRunner()

    async def get_pod_status(self, namespace: str, pod_name: str) -> dict[str, str]:
        result = await self._run_json_command(
            [
                *self._base_args(),
                "get",
                "pod",
                pod_name,
                "--namespace",
                namespace,
                "-o",
                "json",
            ]
        )
        status = cast(dict[str, object], result.get("status", {}))
        spec = cast(dict[str, object], result.get("spec", {}))
        metadata = cast(dict[str, object], result.get("metadata", {}))
        container_statuses = status.get("containerStatuses", [])
        ready_count = 0
        total_containers = 0
        restart_count = 0
        if isinstance(container_statuses, list):
            total_containers = len(container_statuses)
            for container_status in container_statuses:
                if not isinstance(container_status, dict):
                    continue
                if container_status.get("ready") is True:
                    ready_count += 1
                raw_restarts = container_status.get("restartCount", 0)
                if isinstance(raw_restarts, int):
                    restart_count += raw_restarts
        return {
            "name": str(metadata.get("name", pod_name)),
            "namespace": str(metadata.get("namespace", namespace)),
            "node": str(spec.get("nodeName", "")),
            "phase": str(status.get("phase", "Unknown")),
            "pod_ip": str(status.get("podIP", "")),
            "ready": f"{ready_count}/{total_containers}",
            "restart_count": str(restart_count),
        }

    async def get_pod_logs(self, namespace: str, pod_name: str, lines: int = 100) -> str:
        result = await self._run_command(
            [
                *self._base_args(),
                "logs",
                pod_name,
                "--namespace",
                namespace,
                "--tail",
                str(lines),
            ]
        )
        return result.stdout.strip()

    async def get_events(self, namespace: str) -> list[dict[str, str]]:
        result = await self._run_json_command(
            [
                *self._base_args(),
                "get",
                "events",
                "--namespace",
                namespace,
                "-o",
                "json",
            ]
        )
        items = result.get("items", [])
        if not isinstance(items, list):
            return []
        normalized_events: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            involved_object = item.get("involvedObject", {})
            if not isinstance(involved_object, dict):
                involved_object = {}
            normalized_events.append(
                {
                    "type": str(item.get("type", "")),
                    "reason": str(item.get("reason", "")),
                    "message": str(item.get("message", "")),
                    "object": str(involved_object.get("name", "")),
                    "kind": str(involved_object.get("kind", "")),
                    "timestamp": str(
                        item.get("lastTimestamp")
                        or item.get("eventTime")
                        or item.get("firstTimestamp")
                        or ""
                    ),
                }
            )
        return normalized_events

    async def apply_manifest(
        self,
        manifest_yaml: str,
        dry_run: bool = True,
        task_id: str | None = None,
        require_approval: bool = True,
    ) -> str:
        del task_id
        effective_dry_run = dry_run or self.settings.dry_run_mode
        if (
            not effective_dry_run
            and require_approval
            and self.settings.require_human_approval
        ):
            raise ConfigurationError(
                "Live Kubernetes apply is blocked while human approval is required."
            )
        args = [
            *self._base_args(),
            "apply",
            "-f",
            "-",
        ]
        if effective_dry_run:
            args.extend(["--dry-run=server"])
        result = await self._run_command(args, input_text=manifest_yaml)
        return result.stdout.strip()

    def for_kubeconfig(self, kubeconfig_path: str) -> KubectlClient:
        """Clone this client for a different kubeconfig while reusing the runner."""

        updated_settings = self.settings.model_copy(update={"kubeconfig_path": kubeconfig_path})
        return KubectlClient(settings=updated_settings, runner=self.runner)

    async def _run_json_command(self, args: list[str]) -> dict[str, object]:
        result = await self._run_command(args)
        try:
            return cast(dict[str, object], json.loads(result.stdout))
        except json.JSONDecodeError as exc:
            raise KubectlCommandError(
                f"kubectl returned invalid JSON for {' '.join(args)}: {exc}"
            ) from exc

    async def _run_command(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> CommandResult:
        result = await self.runner.run(args, input_text=input_text)
        if result.returncode != 0:
            raise KubectlCommandError(
                f"kubectl command failed ({' '.join(args)}): {result.stderr.strip()}"
            )
        return result

    def _base_args(self) -> list[str]:
        return [
            "--kubeconfig",
            self.settings.kubeconfig_path,
        ]
