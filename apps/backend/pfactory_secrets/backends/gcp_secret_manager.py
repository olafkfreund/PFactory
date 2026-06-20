"""
GCP Secret Manager backend (``gcp-sm://<project>/<secret>[/<version>]`` refs).

Uses ``google-cloud-secret-manager`` (lazily imported) with Application Default
Credentials — the same chain ``core.mcp_credentials`` probes
(``GOOGLE_APPLICATION_CREDENTIALS`` / ADC). Version defaults to ``latest``.
Egress is MANAGED_CLOUD.
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


class GcpSecretManagerBackend(SecretsBackend):
    name = "gcp_secret_manager"

    def available(self) -> bool:
        try:
            import google.cloud.secretmanager  # noqa: F401
        except ImportError:
            return False
        return True

    def egress_class(self) -> EgressClass:
        return EgressClass.MANAGED_CLOUD

    def resolve(self, ref: SecretRef) -> SecretValue:
        try:
            from google.cloud import secretmanager
        except ImportError as exc:
            raise BackendUnavailableError(
                "google-cloud-secret-manager not installed — "
                "`pip install google-cloud-secret-manager`."
            ) from exc

        project = ref.extra.get("project")
        if not project:
            raise SecretsError(f"GCP Secret Manager ref missing project: {ref.raw}")
        version = ref.version or "latest"
        name = f"projects/{project}/secrets/{ref.locator}/versions/{version}"

        try:
            client = secretmanager.SecretManagerServiceClient()
            resp = client.access_secret_version(name=name)
        except Exception as exc:
            if exc.__class__.__name__ in ("NotFound", "PermissionDenied"):
                raise SecretNotFoundError(
                    f"GCP secret {name!r} not found / not accessible"
                ) from exc
            raise SecretsError(f"GCP access_secret_version failed: {exc}") from exc

        value = resp.payload.data.decode("utf-8")
        return SecretValue(
            value=value,
            backend=self.name,
            ref=ref.raw,
            source=f"gcp-sm:{project}/{ref.locator}/{version}",
        )


__all__ = ["GcpSecretManagerBackend"]
