"""AWS KMS backend — wraps the per-org data key under an AWS-managed CMK.

The KMS root key is a customer master key (CMK) created out-of-band by
the operator (Terraform / CloudFormation / console). We never see the
key material; we only pass plaintext data keys to ``kms:Encrypt`` and
receive ciphertext blobs, and vice versa for ``kms:Decrypt``.

This module assumes the boto3 SDK is on the import path. It's listed
in ``apps/web-server/requirements.txt`` for the enterprise distribution.

Configuration (env vars, all read at backend construction time):
  AWS_KMS_KEY_ID       — required. The CMK's key id, ARN, or alias.
                         Example: "alias/pfactory-root" or
                         "arn:aws:kms:eu-west-1:1234:key/abcd-..."
  AWS_REGION           — optional. Inherited from the standard boto3
                         resolution chain if unset.
  AWS_ENDPOINT_URL     — optional. Overrides the KMS endpoint. Used by
                         LocalStack for CI integration tests.

IAM permissions required at runtime: ``kms:Encrypt`` + ``kms:Decrypt``
scoped to the configured CMK. No other KMS permissions are needed.
"""

from __future__ import annotations

import os


class AwsKmsBackend:
    """Wrap/unwrap per-org data keys under an AWS KMS CMK."""

    @classmethod
    def from_env(cls) -> "AwsKmsBackend":
        key_id = os.environ.get("AWS_KMS_KEY_ID")
        if not key_id:
            raise RuntimeError(
                "AwsKmsBackend selected but AWS_KMS_KEY_ID is not set. "
                "Configure the CMK id/arn/alias in the environment."
            )
        return cls(
            key_id=key_id,
            region=os.environ.get("AWS_REGION") or None,
            endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
        )

    def __init__(
        self,
        key_id: str,
        region: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        # Lazy import — boto3 is a heavyweight (~1s cold import) and we
        # don't want it pulled in for fernet-only test runs.
        import boto3

        self._key_id = key_id
        kwargs: dict[str, str] = {}
        if region:
            kwargs["region_name"] = region
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        self._client = boto3.client("kms", **kwargs)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Wrap ``plaintext`` (a per-org 32-byte data key) under the CMK.

        AWS KMS' Encrypt API caps plaintext at 4096 bytes — fine for our
        32-byte data keys but explicitly NOT a general-purpose payload
        encryptor. The data-layer encryption itself is handled by AES-GCM
        in EncryptedString using the unwrapped data key.
        """
        resp = self._client.encrypt(KeyId=self._key_id, Plaintext=plaintext)
        return resp["CiphertextBlob"]

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Unwrap a previously-wrapped data key.

        AWS KMS uses the embedded key reference inside the ciphertext
        blob to identify the CMK — we don't pass KeyId. If the blob was
        wrapped under a different CMK (e.g. after a botched rotation),
        KMS returns ``InvalidCiphertextException``.
        """
        resp = self._client.decrypt(CiphertextBlob=ciphertext)
        return resp["Plaintext"]
