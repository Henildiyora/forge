from __future__ import annotations

import shutil
import socket
import subprocess

import pytest


def _binary_present(name: str) -> bool:
    return shutil.which(name) is not None


def _docker_daemon_available() -> bool:
    if not _binary_present("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _kind_cluster_available() -> bool:
    if not _binary_present("kubectl"):
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


@pytest.fixture(scope="session")
def docker_available() -> bool:
    return _docker_daemon_available()


@pytest.fixture(scope="session")
def kubectl_available() -> bool:
    return _kind_cluster_available()


@pytest.fixture
def free_tcp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port
