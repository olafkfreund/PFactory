"""
pfactory_secrets — pluggable secrets/credentials resolution for PFactory.

Named ``pfactory_secrets`` (not ``secrets``) so it never shadows Python's
stdlib ``secrets`` module, which the backend uses for token generation.

This package is the foundation of the Credential Broker (epic #62): a
``SecretsBackend`` abstraction + a factory that mirrors
``providers/factory.py`` + reference-string routing that mirrors
``phase_config.infer_provider_from_model``. Cloud backends (Vault, Azure KV,
AWS Secrets Manager, GCP Secret Manager) and the ``CredentialBroker`` land in
later child issues; this module ships the abstraction plus the ``env`` and
``localfile`` backends.

A *secret reference* is a compact string identifying one secret in one
backend, e.g. ``env:STAGING_TOKEN`` or ``vault:secret/data/app#token`` — see
``refs.parse_ref``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import ClassVar

# Reuse the egress taxonomy the LLM-provider side already uses, so credential
# destinations are classified with the same vocabulary + badges as byo_llm.
from byo_llm import EgressClass


class SecretsError(RuntimeError):
    """Base class for all secrets-resolution errors."""


class InvalidSecretRefError(SecretsError, ValueError):
    """A secret reference string could not be parsed."""


class BackendUnavailableError(SecretsError):
    """The requested backend exists but cannot operate (missing SDK/tool/config)."""


class SecretNotFoundError(SecretsError):
    """The backend ran but the secret does not exist."""


@dataclass(frozen=True)
class SecretRef:
    """A parsed reference to a single secret.

    Attributes:
        backend: Canonical backend name (``env``, ``localfile``, ``vault``,
            ``azure_keyvault``, ``aws_secrets_manager``, ``gcp_secret_manager``).
        raw: The original reference string (kept for logs/errors).
        locator: Backend-specific primary locator — an env-var name, a file
            path, a Vault path, a secret name, etc.
        field: Optional ``#fragment`` selecting a sub-key inside a structured
            secret (e.g. a key in a sops YAML or a JSON-encoded secret).
        version: Optional version selector (e.g. GCP Secret Manager version).
        extra: Any additional parsed components (e.g. ``{"vault": ...}`` for
            Azure Key Vault, ``{"format": "sops"}`` for local files).
    """

    backend: str
    raw: str
    locator: str
    field: str | None = None
    version: str | None = None
    extra: dict[str, object] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class SecretValue:
    """A resolved secret value.

    The contained ``value`` is sensitive: ``repr`` and ``str`` redact it so it
    cannot leak into logs/tracebacks. Read it explicitly via ``.value``.
    """

    value: str
    backend: str
    ref: str
    source: str = ""  # human description of where it came from (no secret data)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"SecretValue(backend={self.backend!r}, ref={self.ref!r}, "
            f"source={self.source!r}, value=<redacted {len(self.value)} chars>)"
        )

    __str__ = __repr__


class SecretsBackend(ABC):
    """Abstract base for a secrets backend.

    Concrete backends are lazily imported + instantiated by
    ``pfactory_secrets.factory.get_secrets_backend``.
    """

    #: Canonical backend name, set by each subclass.
    name: ClassVar[str] = "abstract"

    @abstractmethod
    def available(self) -> bool:
        """Cheap, non-validating check that this backend *can* operate here
        (required SDK/tool installed, config present). Must not make network
        calls or raise."""

    @abstractmethod
    def resolve(self, ref: SecretRef) -> SecretValue:
        """Resolve ``ref`` to its value, or raise a ``SecretsError`` subclass."""

    def egress_class(self) -> EgressClass:
        """Where resolving from this backend sends data. Local backends override
        to ``LOCAL``; cloud backends classify by endpoint (default: managed)."""
        return EgressClass.MANAGED_CLOUD


__all__ = [
    "BackendUnavailableError",
    "EgressClass",
    "InvalidSecretRefError",
    "SecretNotFoundError",
    "SecretRef",
    "SecretValue",
    "SecretsBackend",
    "SecretsError",
]
