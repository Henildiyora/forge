"""Docker-based dry-run validation.

Copies the target service into a throwaway directory, applies the ProposedFix
(env overrides / file replaces), builds a throwaway image, runs it on an
ephemeral port, hits configured health endpoints, then tears everything down.

``DryRunResult.passed`` comes from real HTTP status codes and the docker
build/run exit codes — never a hardcoded True. If Docker is not available the
call fails loudly rather than silently faking a pass.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from swarm.config import Settings
from swarm.schemas import (
    DryRunResult,
    FixAction,
    FixActionKind,
    ProposedFix,
    SandboxCheck,
    SwarmConfig,
)


class DockerUnavailableError(RuntimeError):
    """Raised when the Docker CLI cannot be used."""


class DockerSandbox:
    """Validate a ProposedFix inside an isolated Docker container."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.docker = settings.docker_binary

    def ensure_available(self) -> None:
        """Fail loudly if Docker is missing or the daemon is down."""

        if shutil.which(self.docker) is None:
            raise DockerUnavailableError(
                f"docker binary {self.docker!r} not found on PATH; "
                "start Docker Desktop or install Docker before running a dry-run"
            )
        result = subprocess.run(
            [self.docker, "info"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if result.returncode != 0:
            raise DockerUnavailableError(
                "docker daemon is not reachable "
                f"(exit {result.returncode}): {(result.stderr or result.stdout)[:300]}"
            )

    def validate(
        self,
        fix: ProposedFix,
        config: SwarmConfig,
        *,
        attempt: int = 1,
    ) -> DryRunResult:
        """Apply ``fix`` in isolation and return a real pass/fail result."""

        started = time.perf_counter()
        logs: list[str] = []
        checks: list[SandboxCheck] = []
        image_tag = f"swarm-dryrun-{config.service}-{int(time.time())}-{attempt}"
        container_name = f"{image_tag}-ctr"
        service_src = self._resolve_service_root(config)
        dockerfile = self._resolve_dockerfile(config, service_src)
        endpoints = list(config.health_endpoints) or ["/healthz", "/checkout"]
        container_port = config.container_port

        if not service_src.exists():
            return DryRunResult(
                passed=False,
                method="docker",
                attempt=attempt,
                exit_code=None,
                duration_seconds=time.perf_counter() - started,
                checks=[],
                logs=f"sandbox service path missing: {service_src}",
                rejection_reason="sandbox_service_missing",
                image_tag=None,
            )

        try:
            self.ensure_available()
        except DockerUnavailableError as exc:
            return DryRunResult(
                passed=False,
                method="docker",
                attempt=attempt,
                exit_code=None,
                duration_seconds=time.perf_counter() - started,
                checks=[],
                logs=str(exc),
                rejection_reason="docker_unavailable",
                image_tag=None,
            )

        with tempfile.TemporaryDirectory(prefix="swarm-dryrun-") as tmp:
            work = Path(tmp) / "service"
            shutil.copytree(service_src, work)
            build_dockerfile = work / "Dockerfile"
            if dockerfile is not None:
                try:
                    rel = dockerfile.resolve().relative_to(service_src.resolve())
                    build_dockerfile = work / rel
                except ValueError:
                    build_dockerfile = work / "Dockerfile"
                    shutil.copy2(dockerfile, build_dockerfile)
                    logs.append(f"copied dockerfile {dockerfile} → {build_dockerfile}")

            env = self._base_env(config)
            env, file_notes = self._apply_fix(work, fix, env)
            logs.extend(file_notes)

            build_cmd = [self.docker, "build", "-t", image_tag]
            if build_dockerfile.exists():
                build_cmd.extend(["-f", str(build_dockerfile)])
            build_cmd.append(str(work))

            build = self._run(build_cmd, timeout=self.settings.sandbox_timeout_seconds)
            logs.append(f"$ {' '.join(build_cmd)} → exit {build.returncode}")
            logs.append(build.stdout)
            logs.append(build.stderr)
            if build.returncode != 0:
                return DryRunResult(
                    passed=False,
                    method="docker",
                    attempt=attempt,
                    exit_code=build.returncode,
                    duration_seconds=time.perf_counter() - started,
                    checks=[
                        SandboxCheck(
                            name="docker_build",
                            passed=False,
                            detail=f"exit {build.returncode}",
                        )
                    ],
                    logs="\n".join(logs),
                    rejection_reason="docker_build_failed",
                    image_tag=image_tag,
                )
            checks.append(SandboxCheck(name="docker_build", passed=True, detail="ok"))

            host_port = _free_port()
            run_cmd = [
                self.docker,
                "run",
                "-d",
                "--rm",
                "--name",
                container_name,
                "-p",
                f"{host_port}:{container_port}",
            ]
            for key, value in env.items():
                run_cmd.extend(["-e", f"{key}={value}"])
            run_cmd.append(image_tag)

            run = self._run(run_cmd, timeout=60)
            logs.append(f"$ docker run → exit {run.returncode}")
            logs.append(run.stdout)
            logs.append(run.stderr)
            if run.returncode != 0:
                self._cleanup(image_tag, container_name)
                return DryRunResult(
                    passed=False,
                    method="docker",
                    attempt=attempt,
                    exit_code=run.returncode,
                    duration_seconds=time.perf_counter() - started,
                    checks=checks
                    + [
                        SandboxCheck(
                            name="docker_run",
                            passed=False,
                            detail=f"exit {run.returncode}",
                        )
                    ],
                    logs="\n".join(logs),
                    rejection_reason="docker_run_failed",
                    image_tag=image_tag,
                )
            checks.append(SandboxCheck(name="docker_run", passed=True, detail="ok"))

            try:
                smoke = self._smoke(host_port, logs, endpoints)
                checks.extend(smoke)
            finally:
                container_logs = self._run(
                    [self.docker, "logs", container_name], timeout=20
                )
                logs.append("--- container logs ---")
                logs.append(container_logs.stdout)
                logs.append(container_logs.stderr)
                self._cleanup(image_tag, container_name)

        passed = all(check.passed for check in checks)
        rejection = None if passed else next(
            (c.detail for c in checks if not c.passed), "smoke_failed"
        )
        return DryRunResult(
            passed=passed,
            method="docker",
            attempt=attempt,
            exit_code=0 if passed else 1,
            duration_seconds=time.perf_counter() - started,
            checks=checks,
            logs="\n".join(logs)[-12000:],
            rejection_reason=None if passed else f"check_failed: {rejection}",
            image_tag=image_tag,
        )

    def _resolve_service_root(self, config: SwarmConfig) -> Path:
        if config.service_root:
            return Path(config.service_root).expanduser().resolve()
        return Path(self.settings.sandbox_service_path).expanduser().resolve()

    def _resolve_dockerfile(self, config: SwarmConfig, service_src: Path) -> Path | None:
        if not config.dockerfile_path:
            candidate = service_src / "Dockerfile"
            return candidate if candidate.exists() else None
        return Path(config.dockerfile_path).expanduser().resolve()

    def _base_env(self, config: SwarmConfig) -> dict[str, str]:
        env = {
            "PAYMENT_TIMEOUT_MS": "2000",
            "FEATURE_CHECKOUT_V2": "false",
            "MAX_RETRIES": "3",
        }
        env.update(config.runtime_env)
        return env

    def _apply_fix(
        self, work: Path, fix: ProposedFix, env: dict[str, str]
    ) -> tuple[dict[str, str], list[str]]:
        notes: list[str] = []
        updated = dict(env)
        for action in fix.actions:
            if action.kind == FixActionKind.ENV_OVERRIDE:
                notes.append(f"apply env_override {action.target}={action.value!r}")
                updated[action.target] = action.value
            elif action.kind == FixActionKind.FILE_REPLACE:
                target = work / action.target
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(action.value, encoding="utf-8")
                notes.append(f"apply file_replace {action.target} ({len(action.value)} bytes)")
            elif action.kind == FixActionKind.REVERT_COMMIT:
                notes.append(
                    f"revert_commit {action.target} is recorded but not auto-applied "
                    "in the docker sandbox; use env_override/file_replace for dry-run"
                )
            else:
                notes.append(f"unsupported action kind: {action.kind}")
        return updated, notes

    def _smoke(
        self,
        host_port: int,
        logs: list[str],
        endpoints: list[str],
    ) -> list[SandboxCheck]:
        base = f"http://127.0.0.1:{host_port}"
        deadline = time.time() + self.settings.sandbox_startup_timeout_seconds
        first = endpoints[0]
        first_ok = False
        last_detail = "not started"
        while time.time() < deadline:
            try:
                response = self._probe(base, first)
                last_detail = f"status={response.status_code} body={response.text[:200]}"
                if 200 <= response.status_code < 300:
                    first_ok = True
                    break
            except httpx.HTTPError as exc:
                last_detail = str(exc)
            time.sleep(0.5)
        logs.append(f"{first}: {last_detail}")
        checks = [
            SandboxCheck(name=f"probe:{first}", passed=first_ok, detail=last_detail),
        ]
        if not first_ok:
            return checks

        for endpoint in endpoints[1:]:
            try:
                response = self._probe(base, endpoint)
                detail = f"status={response.status_code} body={response.text[:200]}"
                checks.append(
                    SandboxCheck(
                        name=f"probe:{endpoint}",
                        passed=200 <= response.status_code < 300,
                        detail=detail,
                    )
                )
                logs.append(f"{endpoint}: {detail}")
            except httpx.HTTPError as exc:
                checks.append(
                    SandboxCheck(name=f"probe:{endpoint}", passed=False, detail=str(exc))
                )
                logs.append(f"{endpoint} error: {exc}")
        return checks

    def _probe(self, base: str, endpoint: str) -> httpx.Response:
        """GET by default; POST /checkout for the demo sandbox contract."""

        url = f"{base}{endpoint}"
        if endpoint.rstrip("/") == "/checkout":
            return httpx.post(url, timeout=5.0)
        return httpx.get(url, timeout=5.0)

    def _cleanup(self, image_tag: str, container_name: str) -> None:
        for cmd in (
            [self.docker, "rm", "-f", container_name],
            [self.docker, "rmi", "-f", image_tag],
        ):
            subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)

    def _run(self, cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def apply_actions_to_env(actions: list[FixAction], env: dict[str, str]) -> dict[str, str]:
    """Pure helper used by tests to preview env overrides without Docker."""

    updated = dict(env)
    for action in actions:
        if action.kind == FixActionKind.ENV_OVERRIDE:
            updated[action.target] = action.value
    return updated
