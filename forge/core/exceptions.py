from __future__ import annotations


class SwarmError(Exception):
    """Base exception for all DevOps Swarm failures."""


class ConfigurationError(SwarmError):
    """Raised when application settings are invalid or incomplete."""


class MessageBusError(SwarmError):
    """Raised for publish, consume, or acknowledgement failures."""


class MessageDecodingError(MessageBusError):
    """Raised when a raw stream message cannot be decoded into a SwarmEvent."""


class SecretNotFoundError(SwarmError):
    """Raised when a secret cannot be located through the configured backend."""


class InsufficientEvidenceError(SwarmError):
    """Raised when an LLM response lacks the evidence required by policy."""
