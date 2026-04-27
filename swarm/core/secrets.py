from __future__ import annotations

import os
from enum import Enum

import structlog
from pydantic import BaseModel, Field

from swarm.core.config import Settings
from swarm.core.exceptions import SecretNotFoundError


class SecretSource(str, Enum):
    """Backends supported by the SecretsManager abstraction."""

    ENV = "env"
    VAULT = "vault"
    K8S = "kubernetes"


class SecretRecord(BaseModel):
    """Resolved secret with metadata about the backend used."""

    key: str = Field(description="Lookup key for the secret value.")
    value: str = Field(description="Resolved secret value.", repr=False)
    source: SecretSource = Field(description="Backend that supplied the secret.")


class SecretsManager:
    """Secrets abstraction that starts with environment-backed lookups."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = structlog.get_logger().bind(component="secrets_manager")

    def get_secret(self, key: str) -> SecretRecord:
        """Resolve a secret from the environment."""

        value = os.getenv(key)
        if value is None:
            raise SecretNotFoundError(f"Secret {key} was not found in the environment.")
        self.logger.info("secret_resolved", key=key, source=SecretSource.ENV.value)
        return SecretRecord(key=key, value=value, source=SecretSource.ENV)

    async def rotate_secret(self, key: str) -> None:
        """Rotation backends are scheduled for the cloud integration sprint."""

        raise NotImplementedError(
            "Secret rotation is planned for the cloud integration sprint "
            "once Vault and K8s backends exist."
        )
