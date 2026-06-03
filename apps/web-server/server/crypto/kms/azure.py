"""Azure Key Vault KMS backend.

Wraps per-org data keys under an Azure Key Vault key via RSA-OAEP. The
key material never leaves Key Vault — we only call the wrap/unwrap
endpoints. RSA-OAEP works on the *standard* Key Vault tier and can
envelope up to ~256 bytes, which is plenty for our 32-byte AES data
keys. AES symmetric wrap (kid-AESKW etc.) requires Managed HSM and is
out of scope for v1 — that's a future env-flag if customers ask.

Configuration (env vars, read at backend construction):
  AZURE_KEYVAULT_URL    — required. e.g. ``https://kv-pfactory.vault.azure.net``
  AZURE_KEYVAULT_KEY    — required. Name (and optional version) of the
                          key. Examples: ``pfactory-root`` or
                          ``pfactory-root/abc123`` for a pinned version.
  AZURE_TENANT_ID       — optional. Standard ``DefaultAzureCredential``
                          chain otherwise (managed identity → env vars →
                          az-CLI → VS Code → IntelliJ).
  AZURE_CLIENT_ID       — optional. For service-principal auth.
  AZURE_CLIENT_SECRET   — optional. For service-principal auth.

IAM contract: the identity needs the ``wrapKey`` and ``unwrapKey``
permissions on the named key (Key Vault access policy or RBAC role
``Key Vault Crypto User``). Nothing else — no Get/List/Create permissions
are required at runtime.

No local emulator: Azurite emulates Azure Storage only, not Key Vault
crypto. The integration test runs against a real tenant in CI when
``AZURE_KEYVAULT_URL`` is wired with valid credentials; locally it
skips. The wire format is opaque Azure-managed bytes — we don't parse
the result, only pass it back to unwrap.
"""

from __future__ import annotations

import os


class AzureKeyVaultBackend:
    """Wrap/unwrap data keys via Azure Key Vault RSA-OAEP."""

    @classmethod
    def from_env(cls) -> "AzureKeyVaultBackend":
        url = os.environ.get("AZURE_KEYVAULT_URL")
        if not url:
            raise RuntimeError(
                "AzureKeyVaultBackend selected but AZURE_KEYVAULT_URL is "
                "not set. Configure the vault URL "
                "(e.g. https://kv-pfactory.vault.azure.net)."
            )
        key_name = os.environ.get("AZURE_KEYVAULT_KEY")
        if not key_name:
            raise RuntimeError(
                "AzureKeyVaultBackend selected but AZURE_KEYVAULT_KEY is "
                "not set. Configure the Key Vault key name."
            )
        return cls(vault_url=url, key_name=key_name)

    def __init__(self, vault_url: str, key_name: str) -> None:
        # Lazy import — the azure-* SDKs are heavyweight and only one
        # backend is active per process. ``DefaultAzureCredential`` walks
        # the standard chain (managed identity → env → CLI → VS Code →
        # etc.), so the operator doesn't have to wire anything beyond
        # AZURE_KEYVAULT_URL when running under managed identity.
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.keys.crypto import CryptographyClient, KeyWrapAlgorithm

        self._wrap_algorithm = KeyWrapAlgorithm.rsa_oaep
        # The CryptographyClient accepts a key identifier — either the
        # full ``https://<vault>/keys/<name>/<version>`` URL or a key
        # name relative to the vault. We assemble the URL form so the
        # backend works whether or not the operator specified a version.
        key_id = f"{vault_url.rstrip('/')}/keys/{key_name}"
        self._client = CryptographyClient(
            key=key_id,
            credential=DefaultAzureCredential(),
        )

    def encrypt(self, plaintext: bytes) -> bytes:
        """Wrap ``plaintext`` (a per-org data key) under the Key Vault key.

        Returns the raw wrapped bytes. Azure embeds the key version
        reference in the wrap result, so decrypt doesn't need a version
        — Key Vault picks the right private key automatically.
        """
        result = self._client.wrap_key(self._wrap_algorithm, plaintext)
        return result.encrypted_key

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Unwrap a previously-wrapped data key.

        Raises ``azure.core.exceptions.HttpResponseError`` for tampered
        or otherwise-invalid ciphertext.
        """
        result = self._client.unwrap_key(self._wrap_algorithm, ciphertext)
        return result.key
