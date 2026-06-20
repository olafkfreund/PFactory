"""
Secrets-backend factory — mirrors ``providers/factory.py``.

A canonical-name registry maps each backend to ``(module_path, class_name)``;
``get_secrets_backend`` resolves aliases, lazily imports the module (so an
absent cloud SDK never breaks startup — it surfaces only when that backend is
actually requested), and instantiates the class.

This foundation issue ships only ``env`` and ``localfile``. Cloud backends
(``vault``, ``azure_keyvault``, ``aws_secrets_manager``, ``gcp_secret_manager``)
are *recognised* by the alias map so reference routing works today; requesting
one before its child issue lands raises a clear ``NotImplementedError``.
"""

from __future__ import annotations

import importlib
from typing import Any

from pfactory_secrets import SecretsBackend

# Canonical backend name -> (module path, class name). Only the implemented
# backends appear here; recognised-but-unimplemented ones are listed in
# ``_PLANNED`` for a helpful error.
_BACKEND_REGISTRY: dict[str, tuple[str, str]] = {
    "env": ("pfactory_secrets.backends.env", "EnvBackend"),
    "localfile": ("pfactory_secrets.backends.localfile", "LocalFileBackend"),
    "vault": ("pfactory_secrets.backends.vault", "VaultBackend"),
    "azure_keyvault": ("pfactory_secrets.backends.azure_keyvault", "AzureKeyVaultBackend"),
    "aws_secrets_manager": (
        "pfactory_secrets.backends.aws_secrets_manager",
        "AwsSecretsManagerBackend",
    ),
    "gcp_secret_manager": (
        "pfactory_secrets.backends.gcp_secret_manager",
        "GcpSecretManagerBackend",
    ),
}

# Recognised backends whose implementation lands in a later child issue.
# (All v1 backends are now implemented.)
_PLANNED: dict[str, str] = {}

# Human-friendly aliases -> canonical name.
_ALIASES: dict[str, str] = {
    "env": "env",
    "environment": "env",
    "localfile": "localfile",
    "local": "localfile",
    "file": "localfile",
    "sops": "localfile",
    "age": "localfile",
    "agenix": "localfile",
    "vault": "vault",
    "hashicorp-vault": "vault",
    "hcv": "vault",
    "azure_keyvault": "azure_keyvault",
    "azurekv": "azure_keyvault",
    "azure-keyvault": "azure_keyvault",
    "akv": "azure_keyvault",
    "aws_secrets_manager": "aws_secrets_manager",
    "aws-sm": "aws_secrets_manager",
    "aws-secrets-manager": "aws_secrets_manager",
    "asm": "aws_secrets_manager",
    "gcp_secret_manager": "gcp_secret_manager",
    "gcp-sm": "gcp_secret_manager",
    "gcp-secret-manager": "gcp_secret_manager",
    "gsm": "gcp_secret_manager",
}


def resolve_canonical(name: str) -> str:
    """Resolve a backend name/alias to its canonical name."""
    canonical = _ALIASES.get(name.strip().lower())
    if canonical is None:
        raise ValueError(f"Unknown secrets backend {name!r}. Supported: {sorted(set(_ALIASES))}")
    return canonical


def get_secrets_backend(name: str, **kwargs: Any) -> SecretsBackend:
    """Instantiate the secrets backend for ``name`` (alias-aware, lazy-imported).

    Raises:
        ValueError: unknown backend name.
        NotImplementedError: a recognised backend whose child issue hasn't landed.
        ImportError: the backend module/SDK failed to import.
    """
    canonical = resolve_canonical(name)
    entry = _BACKEND_REGISTRY.get(canonical)
    if entry is None:
        planned = _PLANNED.get(canonical)
        if planned:
            raise NotImplementedError(
                f"Secrets backend {canonical!r} is not implemented yet — see {planned}."
            )
        raise ValueError(f"No registry entry for backend {canonical!r}")  # pragma: no cover

    module_path, class_name = entry
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:  # pragma: no cover - defensive
        raise ImportError(
            f"Failed to import secrets backend module {module_path!r}: {exc}"
        ) from exc
    backend_cls = getattr(module, class_name)
    return backend_cls(**kwargs)


__all__ = ["get_secrets_backend", "resolve_canonical"]
