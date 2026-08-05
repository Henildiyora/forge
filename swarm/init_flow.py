"""Interactive validators used by ``swarm init``.

Each helper returns a human-readable success summary or raises LiveConfigError
with an actionable message — never a raw stack trace for expected failures.
"""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

import httpx

from swarm.config import REPO_ROOT
from swarm.live import LiveConfigError
from swarm.tools.prometheus import PrometheusClient


def validate_prometheus(url: str, query: str) -> str:
    """Hit Prometheus with the user's query; return a summary of the value."""

    client = PrometheusClient(base_url=url, timeout_seconds=10.0)
    try:
        if not client.is_ready():
            raise LiveConfigError(
                f"Could not reach Prometheus at {url}. "
                "Check the service is running and the port is correct "
                "(try curl {url}/-/ready)."
            )
        try:
            value, summary = client.query_instant(query)
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            raise LiveConfigError(
                f"Prometheus rejected the query (HTTP {exc.response.status_code}). "
                f"Response: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LiveConfigError(
                f"Could not reach Prometheus at {url}: {exc}. "
                "Check the URL and that Prometheus is up."
            ) from exc
        except RuntimeError as exc:
            raise LiveConfigError(str(exc)) from exc
        if value is None:
            raise LiveConfigError(
                f"Query ran but returned no usable value ({summary}). "
                "Double-check metric names with: "
                f"curl {url}/api/v1/label/__name__/values"
            )
        return f"Prometheus OK at {url}\nQuery result: {summary}"
    finally:
        client.close()


def validate_github(repo: str, token: str) -> str:
    """Confirm token can read the repo; show name + latest commit."""

    try:
        from github import Auth, Github, GithubException
    except ImportError as exc:  # pragma: no cover
        raise LiveConfigError("PyGithub is not installed.") from exc

    try:
        gh = Github(auth=Auth.Token(token))
        repository = gh.get_repo(repo)
        commits = repository.get_commits()
        latest = next(iter(commits), None)
    except GithubException as exc:
        message = getattr(exc, "data", {}) or {}
        detail = message.get("message") if isinstance(message, dict) else str(exc)
        raise LiveConfigError(
            f"GitHub API error for {repo!r}: {detail}. "
            "Check the owner/name and that GITHUB_TOKEN has repo read access. "
            "Create a token at https://github.com/settings/tokens (repo scope)."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise LiveConfigError(f"GitHub request failed: {exc}") from exc

    if latest is None:
        return f"GitHub OK: {repository.full_name} (no commits yet)"
    sha = str(latest.sha)[:8]
    msg = str(latest.commit.message).splitlines()[0]
    return f"GitHub OK: {repository.full_name}\nLatest commit: {sha} — {msg}"


def validate_docker_build(dockerfile: Path, context: Path, tag: str = "swarm-init-check") -> str:
    """Run ``docker build`` and return the last log lines on success."""

    if not context.exists() or not context.is_dir():
        raise LiveConfigError(
            f"Build context not found: {context}. Point service_build_context at "
            "the directory that contains your service sources."
        )
    if not dockerfile.exists():
        raise LiveConfigError(
            f"Dockerfile not found: {dockerfile}. Point service_dockerfile_path "
            "at an existing Dockerfile (no changes needed to that file)."
        )

    cmd = [
        "docker",
        "build",
        "-t",
        tag,
        "-f",
        str(dockerfile),
        str(context),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=300
        )
    except FileNotFoundError as exc:
        raise LiveConfigError(
            "docker binary not found on PATH. Install Docker Desktop and retry."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise LiveConfigError("docker build timed out after 300s.") from exc

    log_tail = (result.stdout or "")[-800:] + "\n" + (result.stderr or "")[-800:]
    if result.returncode != 0:
        raise LiveConfigError(
            f"docker build failed (exit {result.returncode}). Last output:\n{log_tail}"
        )
    return f"docker build OK (tag={tag})\nLast lines:\n{log_tail.strip()[-500:]}"


def validate_health_endpoints(
    image_tag: str,
    endpoints: list[str],
    *,
    container_port: int = 8080,
    runtime_env: dict[str, str] | None = None,
) -> str:
    """Run the built image and GET (or demo POST /checkout) each endpoint."""

    host_port = _free_port()
    name = f"swarm-init-health-{host_port}"
    run_cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "-p",
        f"{host_port}:{container_port}",
    ]
    for key, value in (runtime_env or {}).items():
        run_cmd.extend(["-e", f"{key}={value}"])
    # Demo target_service is unhealthy with default broken env from compose;
    # for init against target_service, inject healthy knobs unless caller set them.
    env = dict(runtime_env or {})
    if "PAYMENT_TIMEOUT_MS" not in env:
        run_cmd.extend(["-e", "PAYMENT_TIMEOUT_MS=2000"])
    if "FEATURE_CHECKOUT_V2" not in env:
        run_cmd.extend(["-e", "FEATURE_CHECKOUT_V2=false"])
    if "MAX_RETRIES" not in env:
        run_cmd.extend(["-e", "MAX_RETRIES=3"])
    run_cmd.append(image_tag)

    started = subprocess.run(run_cmd, capture_output=True, text=True, check=False, timeout=60)
    if started.returncode != 0:
        raise LiveConfigError(
            f"docker run failed (exit {started.returncode}): "
            f"{(started.stderr or started.stdout)[:400]}"
        )

    lines: list[str] = []
    try:
        deadline = time.time() + 30
        last_error = "not started"
        ready = False
        first = endpoints[0]
        while time.time() < deadline:
            try:
                response = _probe(host_port, first)
                last_error = f"{response.status_code} {response.text[:120]}"
                if 200 <= response.status_code < 300:
                    ready = True
                    lines.append(f"{first} → {last_error}")
                    break
            except httpx.HTTPError as exc:
                last_error = str(exc)
            time.sleep(0.5)
        if not ready:
            raise LiveConfigError(
                f"Health check never succeeded for {first}: {last_error}. "
                "Confirm the container listens on port "
                f"{container_port} and the path is correct."
            )
        for endpoint in endpoints[1:]:
            response = _probe(host_port, endpoint)
            snippet = f"{response.status_code} {response.text[:120]}"
            lines.append(f"{endpoint} → {snippet}")
            if not (200 <= response.status_code < 300):
                raise LiveConfigError(
                    f"Endpoint {endpoint} returned {snippet}. "
                    "Fix the path or the service before continuing."
                )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    return "Health endpoints OK:\n" + "\n".join(lines)


def resolve_path(raw: str) -> Path:
    """Resolve a user-entered path relative to the repo root when needed."""

    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    else:
        path = path.resolve()
    return path


def _probe(host_port: int, endpoint: str) -> httpx.Response:
    url = f"http://127.0.0.1:{host_port}{endpoint}"
    if endpoint.rstrip("/") == "/checkout":
        return httpx.post(url, timeout=5.0)
    return httpx.get(url, timeout=5.0)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
