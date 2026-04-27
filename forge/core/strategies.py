from __future__ import annotations

from enum import Enum


class DeploymentStrategy(str, Enum):
    """Supported deployment strategies produced by the FORGE conversation layer."""

    DOCKER_COMPOSE = "docker_compose"
    KUBERNETES = "kubernetes"
    SERVERLESS = "serverless"
    EXTEND_EXISTING = "extend_existing"
    CICD_ONLY = "cicd_only"
