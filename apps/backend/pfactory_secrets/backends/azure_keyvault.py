"""
Azure Key Vault backend (``azurekv://<vault>/<secret>`` refs).

Uses ``azure-identity`` (``DefaultAzureCredential`` — env service principal,
Managed Identity, Azure CLI…) + ``azure-keyvault-secrets`` (``SecretClient``),
both lazily imported. The vault URL is derived as
``https://<vault>.vault.azure.net``. Egress is MANAGED_CLOUD.
"""

from __future__ import annotations

from pfactory_secrets import (
    BackendUnavailableError,
    EgressClass,
    SecretNotFoundError,
    SecretRef,
    SecretsBackend,
    SecretsError,
    SecretValue,
)

_VAULT_URL_TMPL = "https://{vault}.vault.azure.net"


class AzureKeyVaultBackend(SecretsBackend):
    name = "azure_keyvault"

    def available(self) -> bool:
        try:
            import azure.identity  # noqa: F401
            import azure.keyvault.secrets  # noqa: F401
        except ImportError:
            return False
        return True

    def egress_class(self) -> EgressClass:
        return EgressClass.MANAGED_CLOUD

    def resolve(self, ref: SecretRef) -> SecretValue:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError as exc:
            raise BackendUnavailableError(
                "azure-identity / azure-keyvault-secrets not installed — "
                "`pip install azure-identity azure-keyvault-secrets`."
            ) from exc

        vault = ref.extra.get("vault")
        if not vault:
            raise SecretsError(f"Azure Key Vault ref missing vault name: {ref.raw}")
        url = _VAULT_URL_TMPL.format(vault=vault)
        try:
            client = SecretClient(vault_url=url, credential=DefaultAzureCredential())
            secret = client.get_secret(ref.locator)
        except Exception as exc:  # noqa: BLE001 - azure raises various types
            if exc.__class__.__name__ == "ResourceNotFoundError":
                raise SecretNotFoundError(
                    f"Secret {ref.locator!r} not found in vault {vault!r}"
                ) from exc
            raise SecretsError(
                f"Azure Key Vault get_secret({ref.locator!r}) failed: {exc}"
            ) from exc

        return SecretValue(
            value=secret.value, backend=self.name, ref=ref.raw,
            source=f"azurekv:{vault}/{ref.locator}",
        )


__all__ = ["AzureKeyVaultBackend"]
