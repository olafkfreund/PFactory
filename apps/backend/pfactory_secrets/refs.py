"""
Secret-reference parsing + backend routing.

Mirrors ``phase_config.infer_provider_from_model`` / ``strip_provider_prefix``:
the *scheme* of a reference string decides which backend resolves it. Two
syntactic families coexist (each mirrors how the backend natively addresses a
secret), so parsing is done **per scheme**, not with one regex:

    scheme:locator[#field]          env / file / sops / age / agenix / vault
    scheme://authority-or-path      azurekv / aws-sm / gcp-sm

Examples::

    env:STAGING_API_TOKEN
    file:/run/secrets/token
    sops:secrets.enc.yaml#api_token
    agenix:staging-token.age
    vault:secret/data/pfactory/staging#api_token
    azurekv://my-vault/STAGING-API-TOKEN
    aws-sm://staging/api#token
    gcp-sm://my-project/staging-api-token        (optionally /<version>)
"""

from __future__ import annotations

from pfactory_secrets import InvalidSecretRefError, SecretRef

# scheme (as written in the ref) -> canonical backend name.
_SCHEME_TO_BACKEND: dict[str, str] = {
    "env": "env",
    "file": "localfile",
    "sops": "localfile",
    "age": "localfile",
    "agenix": "localfile",
    "vault": "vault",
    "azurekv": "azure_keyvault",
    "aws-sm": "aws_secrets_manager",
    "gcp-sm": "gcp_secret_manager",
}

# Schemes whose body is ``//authority/path`` rather than ``locator#field``.
_AUTHORITY_SCHEMES = {"azurekv", "aws-sm", "gcp-sm"}


def infer_backend_from_ref(raw: str) -> str:
    """Return the canonical backend name for a reference string.

    Raises ``InvalidSecretRefError`` for an unknown/empty scheme.
    """
    scheme = _scheme_of(raw)
    backend = _SCHEME_TO_BACKEND.get(scheme)
    if backend is None:
        raise InvalidSecretRefError(
            f"Unknown secret-ref scheme {scheme!r} in {raw!r}. "
            f"Known schemes: {sorted(_SCHEME_TO_BACKEND)}"
        )
    return backend


def parse_ref(raw: str) -> SecretRef:
    """Parse a reference string into a ``SecretRef`` using a per-scheme parser."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidSecretRefError(f"Empty/invalid secret ref: {raw!r}")
    raw = raw.strip()
    scheme = _scheme_of(raw)
    backend = _SCHEME_TO_BACKEND.get(scheme)
    if backend is None:
        raise InvalidSecretRefError(
            f"Unknown secret-ref scheme {scheme!r} in {raw!r}. "
            f"Known schemes: {sorted(_SCHEME_TO_BACKEND)}"
        )

    body = raw[len(scheme) + 1:]  # strip ``scheme:``

    if scheme in _AUTHORITY_SCHEMES:
        return _parse_authority(scheme, backend, body, raw)
    return _parse_locator(scheme, backend, body, raw)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _scheme_of(raw: str) -> str:
    scheme, sep, _ = raw.strip().partition(":")
    if not sep:
        raise InvalidSecretRefError(
            f"Secret ref {raw!r} has no scheme (expected '<scheme>:...')"
        )
    return scheme.lower()


def _split_field(body: str) -> tuple[str, str | None]:
    """Split a trailing ``#field`` fragment off a locator."""
    locator, sep, frag = body.partition("#")
    return locator, (frag or None) if sep else None


def _parse_locator(scheme: str, backend: str, body: str, raw: str) -> SecretRef:
    """``scheme:locator[#field]`` family (env / file / sops / age / agenix / vault)."""
    locator, fld = _split_field(body)
    if not locator:
        raise InvalidSecretRefError(f"Secret ref {raw!r} is missing a locator")
    extra: dict = {}
    if backend == "localfile":
        # Preserve which on-disk format the file is in so the localfile backend
        # knows whether to read plaintext or decrypt (sops/age/agenix).
        extra["format"] = scheme
    return SecretRef(backend=backend, raw=raw, locator=locator, field=fld, extra=extra)


def _parse_authority(scheme: str, backend: str, body: str, raw: str) -> SecretRef:
    """``scheme://...`` family (azurekv / aws-sm / gcp-sm)."""
    if not body.startswith("//"):
        raise InvalidSecretRefError(
            f"Secret ref {raw!r} must use '{scheme}://...' form"
        )
    rest = body[2:]
    if not rest:
        raise InvalidSecretRefError(f"Secret ref {raw!r} is missing a path")

    if scheme == "azurekv":
        # azurekv://<vault-name>/<secret-name>[#field]
        path, fld = _split_field(rest)
        vault, sep, name = path.partition("/")
        if not sep or not vault or not name:
            raise InvalidSecretRefError(
                f"Azure Key Vault ref must be 'azurekv://<vault>/<secret>': {raw!r}"
            )
        return SecretRef(
            backend=backend, raw=raw, locator=name, field=fld, extra={"vault": vault}
        )

    if scheme == "aws-sm":
        # aws-sm://<secret-name-which-may-contain-slashes>[#json-field]
        path, fld = _split_field(rest)
        if not path:
            raise InvalidSecretRefError(f"AWS Secrets Manager ref missing name: {raw!r}")
        return SecretRef(backend=backend, raw=raw, locator=path, field=fld)

    if scheme == "gcp-sm":
        # gcp-sm://<project>/<secret>[/<version>]
        parts = rest.split("/")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise InvalidSecretRefError(
                f"GCP Secret Manager ref must be 'gcp-sm://<project>/<secret>[/<version>]': {raw!r}"
            )
        project, secret = parts[0], parts[1]
        version = parts[2] if len(parts) > 2 and parts[2] else None
        return SecretRef(
            backend=backend, raw=raw, locator=secret, version=version,
            extra={"project": project},
        )

    raise InvalidSecretRefError(f"Unhandled authority scheme {scheme!r}")  # pragma: no cover


__all__ = ["parse_ref", "infer_backend_from_ref"]
