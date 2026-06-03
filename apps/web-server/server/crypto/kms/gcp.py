"""Google Cloud KMS backend.

Wraps per-org data keys under a Cloud KMS CryptoKey via the symmetric
``Encrypt`` / ``Decrypt`` API. The key material never leaves Cloud KMS;
we send plaintext over the wire (TLS) and receive opaque ciphertext.

Configuration (env vars, read at backend construction):
  GCP_KMS_KEY_NAME      — required. Fully-qualified key resource name::

      projects/{project}/locations/{loc}/keyRings/{ring}/cryptoKeys/{key}

      You can also pin a specific key version with
      ``.../cryptoKeys/{key}/cryptoKeyVersions/{n}``, but symmetric keys
      typically don't need it — KMS uses the primary version automatically.

  GOOGLE_APPLICATION_CREDENTIALS — optional. Path to a service-account
                                   JSON key file. If unset, ADC walks
                                   the standard chain (gcloud auth →
                                   metadata service on GCE/GKE/Cloud Run
                                   → workload identity).

IAM contract: the identity needs the
``roles/cloudkms.cryptoKeyEncrypterDecrypter`` role on the configured
key. Nothing else — no list/get/create permissions are required at
runtime.

No local emulator: Google does not ship an official Cloud KMS emulator.
A third-party ``cloud-kms-emulator`` exists but diverges from real KMS
behavior in audit-relevant ways (CRC32C verification, key-version
semantics), so for fintech-target deployments we don't use it. The
integration test runs only when ``GCP_KMS_KEY_NAME`` is wired against a
real project.

Best-practice: every Cloud KMS Encrypt/Decrypt request carries a CRC32C
checksum of the plaintext/ciphertext that the server verifies. We
compute it on the way in and assert the server agrees on the way back.
"""

from __future__ import annotations

import os


class GcpKmsBackend:
    """Wrap/unwrap data keys via Cloud KMS symmetric Encrypt/Decrypt."""

    @classmethod
    def from_env(cls) -> "GcpKmsBackend":
        key_name = os.environ.get("GCP_KMS_KEY_NAME")
        if not key_name:
            raise RuntimeError(
                "GcpKmsBackend selected but GCP_KMS_KEY_NAME is not set. "
                "Configure the fully-qualified Cloud KMS key resource name "
                "(projects/.../cryptoKeys/...)."
            )
        return cls(key_name=key_name)

    def __init__(self, key_name: str) -> None:
        # Lazy import — google-cloud-kms pulls in grpcio (~10 MB) and we
        # don't want that on non-GCP deployments.
        from google.cloud import kms

        self._key_name = key_name
        self._client = kms.KeyManagementServiceClient()

    @staticmethod
    def _crc32c(data: bytes) -> int:
        """Compute Cloud KMS' wire-format CRC32C for ``data``.

        Cloud KMS rejects requests whose plaintext_crc32c or
        ciphertext_crc32c don't match — that's the SDK's recommended
        defense against on-the-wire corruption. ``google-crc32c`` is a
        declared dependency in apps/web-server/requirements.txt.
        """
        import google_crc32c
        c = google_crc32c.Checksum()
        c.update(data)
        return int.from_bytes(c.digest(), "big")

    def encrypt(self, plaintext: bytes) -> bytes:
        """Wrap ``plaintext`` (a per-org data key) under the Cloud KMS key.

        Returns Cloud KMS' opaque ciphertext bytes. The key reference is
        embedded in the ciphertext, so decrypt doesn't need a version.
        """
        plaintext_crc32c = self._crc32c(plaintext)
        resp = self._client.encrypt(
            request={
                "name": self._key_name,
                "plaintext": plaintext,
                "plaintext_crc32c": plaintext_crc32c,
            }
        )
        # Sanity-check round-trip integrity: Cloud KMS returns the CRC32C
        # of the ciphertext for us to verify. Mismatches indicate
        # corruption between KMS and the client.
        if not resp.verified_plaintext_crc32c:
            raise RuntimeError(
                "Cloud KMS did not verify the plaintext CRC32C — "
                "possible in-flight corruption"
            )
        if resp.ciphertext_crc32c.value != self._crc32c(resp.ciphertext):
            raise RuntimeError(
                "Cloud KMS ciphertext CRC32C mismatch — "
                "possible response corruption"
            )
        return resp.ciphertext

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Unwrap a previously-wrapped data key.

        Raises ``google.api_core.exceptions.InvalidArgument`` (or a
        related ``GoogleAPIError`` subclass) for tampered or
        version-archived ciphertext.
        """
        ciphertext_crc32c = self._crc32c(ciphertext)
        resp = self._client.decrypt(
            request={
                "name": self._key_name,
                "ciphertext": ciphertext,
                "ciphertext_crc32c": ciphertext_crc32c,
            }
        )
        if resp.plaintext_crc32c.value != self._crc32c(resp.plaintext):
            raise RuntimeError(
                "Cloud KMS plaintext CRC32C mismatch — "
                "possible response corruption"
            )
        return resp.plaintext
