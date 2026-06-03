"""HashiCorp Vault Transit KMS backend.

Wraps per-org data keys via Vault's Transit secrets engine. The Transit
engine never returns the named key material — only opaque ciphertexts
keyed by a version-prefixed wire format::

    vault:v<integer>:<base64-of-aead-ciphertext>

The integer is the Transit key version. Rotating the named key in Vault
bumps the version; old ciphertexts continue to decrypt indefinitely
(until the operator explicitly archives + deletes the version), which
is the property we want for P2.5's online rotation.

Configuration (env vars, read at backend construction):
  VAULT_ADDR              — required. e.g. ``http://vault.internal:8200``
  VAULT_TOKEN             — required at runtime. The token must have
                            ``encrypt`` + ``decrypt`` capabilities on
                            ``transit/encrypt/<key>`` and
                            ``transit/decrypt/<key>`` paths.
  VAULT_TRANSIT_KEY       — optional. Name of the Transit key; defaults
                            to ``pfactory-root``.
  VAULT_TRANSIT_MOUNT     — optional. Mount path of the Transit engine;
                            defaults to ``transit``.
  VAULT_NAMESPACE         — optional. For Vault Enterprise namespacing.

The Backend protocol contract is ``bytes -> bytes``. Vault's wire
format is a string, so we UTF-8-encode the ciphertext on the way out
and decode on the way in. Plaintext crossing the Vault HTTP boundary
is base64-encoded as Vault requires.
"""

from __future__ import annotations

import base64
import os


_DEFAULT_KEY = "pfactory-root"
_DEFAULT_MOUNT = "transit"


class VaultTransitBackend:
    """Wrap/unwrap data keys via Vault Transit ``encrypt``/``decrypt``."""

    @classmethod
    def from_env(cls) -> "VaultTransitBackend":
        addr = os.environ.get("VAULT_ADDR")
        if not addr:
            raise RuntimeError(
                "VaultTransitBackend selected but VAULT_ADDR is not set. "
                "Configure the Vault server URL in the environment."
            )
        token = os.environ.get("VAULT_TOKEN")
        if not token:
            raise RuntimeError(
                "VaultTransitBackend selected but VAULT_TOKEN is not set. "
                "Provision a Vault token with transit encrypt/decrypt caps."
            )
        return cls(
            addr=addr,
            token=token,
            key_name=os.environ.get("VAULT_TRANSIT_KEY") or _DEFAULT_KEY,
            mount_point=os.environ.get("VAULT_TRANSIT_MOUNT") or _DEFAULT_MOUNT,
            namespace=os.environ.get("VAULT_NAMESPACE") or None,
        )

    def __init__(
        self,
        addr: str,
        token: str,
        key_name: str = _DEFAULT_KEY,
        mount_point: str = _DEFAULT_MOUNT,
        namespace: str | None = None,
    ) -> None:
        # Lazy import — hvac isn't a hard dependency for non-Vault deployments.
        import hvac

        self._key_name = key_name
        self._mount_point = mount_point
        client_kwargs: dict[str, str] = {"url": addr, "token": token}
        if namespace:
            client_kwargs["namespace"] = namespace
        self._client = hvac.Client(**client_kwargs)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Wrap ``plaintext`` under the named Transit key.

        Vault Transit's API takes base64-encoded plaintext over HTTP and
        returns a ``vault:v<version>:<base64-ciphertext>`` string. We
        UTF-8-encode the returned string into bytes so the data layer
        can treat all backends uniformly.
        """
        b64_plaintext = base64.b64encode(plaintext).decode("ascii")
        resp = self._client.secrets.transit.encrypt_data(
            name=self._key_name,
            plaintext=b64_plaintext,
            mount_point=self._mount_point,
        )
        return resp["data"]["ciphertext"].encode("utf-8")

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Unwrap a previously-wrapped data key.

        Vault embeds the key-version reference inside the ciphertext, so
        we don't pass a version — Vault picks the right decryption key
        from the version prefix.
        """
        wire = ciphertext.decode("utf-8")
        resp = self._client.secrets.transit.decrypt_data(
            name=self._key_name,
            ciphertext=wire,
            mount_point=self._mount_point,
        )
        return base64.b64decode(resp["data"]["plaintext"])
