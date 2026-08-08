"""
HashiCorp Vault backend (``vault:`` refs) — uses ``hvac`` (lazily imported).

Ref form: ``vault:<path>#<field>`` where ``<path>`` is the full read path
(e.g. ``secret/data/pfactory/staging`` for a KV-v2 mount) and ``#field`` selects
a key from the returned data map. Connection comes from ``VAULT_ADDR`` +
``VAULT_TOKEN`` (or an operator-configured token), matching the Vault CLI's
conventions.

The SDK is imported only inside ``available()`` / ``resolve()`` so an absent
``hvac`` degrades this backend to unavailable rather than breaking startup.
Egress is classified from ``VAULT_ADDR`` (Vault is typically self-hosted).
"""

from __future__ import annotations

import os

from pfactory_secrets import (
    BackendUnavailableError,
    EgressClass,
    SecretNotFoundError,
    SecretRef,
    SecretsBackend,
    SecretsError,
    SecretValue,
)


class VaultBackend(SecretsBackend):
    name = "vault"

    def __init__(self, addr: str | None = None, token: str | None = None) -> None:
        self._addr = addr or os.environ.get("VAULT_ADDR", "").strip()
        self._token = token or os.environ.get("VAULT_TOKEN", "").strip()

    def available(self) -> bool:
        if not self._addr:
            return False
        try:
            import hvac  # noqa: F401
        except ImportError:
            return False
        return True

    def egress_class(self) -> EgressClass:
        from byo_llm import host_is_local

        host = _host_of(self._addr)
        if host_is_local(host):
            return EgressClass.LOCAL
        return EgressClass.SELF_HOSTED

    def resolve(self, ref: SecretRef) -> SecretValue:
        try:
            import hvac
        except ImportError as exc:
            raise BackendUnavailableError(
                "hvac not installed — `pip install hvac` to use vault: refs."
            ) from exc
        if not self._addr:
            raise BackendUnavailableError("VAULT_ADDR is not set.")

        client = hvac.Client(url=self._addr, token=self._token or None)
        try:
            resp = client.read(ref.locator)
        except Exception as exc:
            raise SecretsError(f"Vault read of {ref.locator!r} failed: {exc}") from exc
        # hvac.Client.read() is `dict | Response | None`: the parsed JSON body on
        # the normal path, None when the path is missing, and the RAW
        # requests.Response when the body is not JSON — a proxy error page, an
        # HTML 502 from a load balancer in front of Vault, a 204 No Content.
        # Response must be caught here, ahead of the falsy check: it is truthy
        # for a 2xx (so it reached .get() below and raised AttributeError
        # OUTSIDE the try above, bypassing the error type every caller handles)
        # and falsy for an error status (so it was reported as a missing path,
        # which is a different and equally wrong answer). See #480.
        if resp is not None and not isinstance(resp, dict):
            raise SecretsError(
                f"Vault returned a non-JSON response for {ref.locator!r} "
                f"(HTTP {getattr(resp, 'status_code', 'unknown')}); check for a "
                "proxy or load balancer in front of Vault."
            )
        if not resp:
            raise SecretNotFoundError(f"Vault path not found: {ref.locator}")

        data = resp.get("data", {})
        # KV-v2 nests the secret under data.data; KV-v1 / generic is flat.
        if isinstance(data.get("data"), dict):
            data = data["data"]

        value = _select(data, ref.field, ref.locator)
        return SecretValue(
            value=value, backend=self.name, ref=ref.raw, source=f"vault:{ref.locator}"
        )


def _select(data: dict, field: str | None, path: str) -> str:
    if field is not None:
        if field not in data:
            raise SecretNotFoundError(f"Field {field!r} not in Vault path {path}")
        return str(data[field])
    # No field: if there's exactly one key, return it; else require a field.
    if len(data) == 1:
        return str(next(iter(data.values())))
    raise SecretsError(f"Vault path {path} has multiple keys {sorted(data)}; specify '#<field>'.")


def _host_of(addr: str) -> str | None:
    from urllib.parse import urlparse

    try:
        return urlparse(addr).hostname
    except ValueError:  # pragma: no cover
        return None


__all__ = ["VaultBackend"]
