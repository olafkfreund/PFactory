"""P2.4 — per-backend KMS round-trip tests (aws_kms / azure_kv / gcp_kms / vault_transit)."""

import os

import pytest

from tests.secrets.helpers import kms_backend_available, reimport_crypto

IN_CI = os.environ.get("CI", "").lower() == "true"

# AWS KMS test runs whenever AWS_ENDPOINT_URL is set — that signals
# LocalStack is reachable. Locally: spin LocalStack on :4566 and export
# AWS_ENDPOINT_URL=http://localhost:4566. CI's secrets-acceptance job
# sets this via the LocalStack service container.
AWS_LOCALSTACK_URL = os.environ.get("AWS_ENDPOINT_URL")


@pytest.mark.secrets
@pytest.mark.slow
@pytest.mark.skipif(
    not AWS_LOCALSTACK_URL,
    reason="AWS_ENDPOINT_URL not set; AWS KMS test requires LocalStack",
)
@pytest.mark.skipif(not kms_backend_available("aws_kms"), reason="boto3 not installed")
def test_aws_kms_roundtrip() -> None:
    """envelope-encrypt + decrypt via AWS KMS (LocalStack-backed in CI).

    Steps:
      1. Create a CMK in the LocalStack KMS endpoint (one-time per test).
      2. Re-import server.crypto with APP_KMS_BACKEND=aws_kms + AWS_KMS_KEY_ID.
      3. Wrap a fresh 32-byte data key, then unwrap.
      4. Assert plaintext round-trips and ciphertext is bigger than plaintext
         (AWS KMS' wrapped blob has metadata + auth tag — at least 64 bytes).
      5. Assert tampered ciphertext is rejected by KMS (InvalidCiphertextException).
    """
    import boto3
    from botocore.exceptions import ClientError

    # LocalStack scopes KMS keys by (account_id, region). The account id
    # is derived from the AWS_ACCESS_KEY_ID — so the fixture's client and
    # the backend's client MUST use identical credentials and region or
    # the second one won't see the key the first one created. We set the
    # env explicitly (not via setdefault) so leakage from earlier tests
    # can't shift the account id mid-test.
    os.environ["AWS_ACCESS_KEY_ID"] = "pfactory-test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "pfactory-test"
    os.environ["AWS_REGION"] = "us-east-1"

    raw_kms = boto3.client(
        "kms",
        endpoint_url=AWS_LOCALSTACK_URL,
        region_name="us-east-1",
        aws_access_key_id="pfactory-test",
        aws_secret_access_key="pfactory-test",
    )
    key_id = raw_kms.create_key(Description="pfactory-test-cmk")["KeyMetadata"]["KeyId"]

    # Sanity: same client should see the key it just created.
    listed = [k["KeyId"] for k in raw_kms.list_keys()["Keys"]]
    assert key_id in listed, f"LocalStack lost the key it just created: {listed}"

    # Now drive the backend through our factory.
    reimport_crypto({
        "APP_KMS_BACKEND": "aws_kms",
        "AWS_KMS_KEY_ID": key_id,
        "AWS_ENDPOINT_URL": AWS_LOCALSTACK_URL,
    })
    from server.crypto import get_backend  # noqa: E402

    backend = get_backend()

    # 32-byte plaintext = a typical per-org data key.
    plaintext = b"\x42" * 32
    ciphertext = backend.encrypt(plaintext)

    assert isinstance(ciphertext, bytes), "encrypt must return bytes"
    assert len(ciphertext) > len(plaintext), \
        "AWS KMS wrap should add metadata + auth tag"
    assert plaintext not in ciphertext, \
        "plaintext key bytes must not appear inside the wrapped blob"

    decrypted = backend.decrypt(ciphertext)
    assert decrypted == plaintext, "round-trip must recover the data key exactly"

    # Tamper test: flip a byte in the middle of the blob, KMS must reject.
    tampered = bytearray(ciphertext)
    tampered[len(tampered) // 2] ^= 0xFF
    with pytest.raises(ClientError) as excinfo:
        backend.decrypt(bytes(tampered))
    assert excinfo.value.response["Error"]["Code"] in {
        "InvalidCiphertextException",
        "KMSInvalidStateException",
    }, f"unexpected error code: {excinfo.value.response['Error']['Code']}"


AZURE_KEYVAULT_URL = os.environ.get("AZURE_KEYVAULT_URL")
AZURE_KEYVAULT_KEY = os.environ.get("AZURE_KEYVAULT_KEY")


@pytest.mark.secrets
@pytest.mark.slow
@pytest.mark.skipif(
    not (AZURE_KEYVAULT_URL and AZURE_KEYVAULT_KEY),
    reason=(
        "AZURE_KEYVAULT_URL + AZURE_KEYVAULT_KEY not set; Azure Key Vault "
        "has no faithful local emulator (Azurite is Storage-only), so this "
        "test runs only when a real Key Vault is wired"
    ),
)
@pytest.mark.skipif(not kms_backend_available("azure_kv"), reason="azure-keyvault-keys not installed")
def test_azure_kv_roundtrip() -> None:
    """envelope-encrypt + decrypt via Azure Key Vault (real tenant only).

    Runs only when an operator wires AZURE_KEYVAULT_URL + AZURE_KEYVAULT_KEY
    against a real tenant. The named key MUST exist and the caller MUST
    have ``wrapKey`` + ``unwrapKey`` permissions (Key Vault Crypto User
    role or equivalent access-policy entries).

    Steps:
      1. Re-import server.crypto with APP_KMS_BACKEND=azure_kv.
      2. Round-trip a 32-byte data key through wrap/unwrap.
      3. Assert plaintext is recovered and never appears in the wire blob.
      4. Tamper-reject: flip a byte and assert Azure raises one of the
         HttpResponseError / ServiceRequestError family.
    """
    from azure.core.exceptions import AzureError

    reimport_crypto({
        "APP_KMS_BACKEND": "azure_kv",
        "AZURE_KEYVAULT_URL": AZURE_KEYVAULT_URL,
        "AZURE_KEYVAULT_KEY": AZURE_KEYVAULT_KEY,
    })
    from server.crypto import get_backend  # noqa: E402

    backend = get_backend()

    plaintext = b"\xcd" * 32
    ciphertext = backend.encrypt(plaintext)

    assert isinstance(ciphertext, bytes), "encrypt must return bytes"
    assert len(ciphertext) >= 256, \
        f"RSA-OAEP wrap of a 2048-bit key should be 256+ bytes; got {len(ciphertext)}"
    assert plaintext not in ciphertext, \
        "plaintext bytes must not appear inside the wrapped blob"

    decrypted = backend.decrypt(ciphertext)
    assert decrypted == plaintext, "round-trip must recover the data key exactly"

    # Tamper: flip a byte in the middle of the wrapped blob.
    tampered = bytearray(ciphertext)
    tampered[len(tampered) // 2] ^= 0xFF
    with pytest.raises(AzureError):
        backend.decrypt(bytes(tampered))


GCP_KMS_KEY_NAME = os.environ.get("GCP_KMS_KEY_NAME")


@pytest.mark.secrets
@pytest.mark.slow
@pytest.mark.skipif(
    not GCP_KMS_KEY_NAME,
    reason=(
        "GCP_KMS_KEY_NAME not set; Google does not ship an official Cloud "
        "KMS emulator, so this test runs only when a real key resource "
        "name and ADC credentials are wired"
    ),
)
@pytest.mark.skipif(not kms_backend_available("gcp_kms"), reason="google-cloud-kms not installed")
def test_gcp_kms_roundtrip() -> None:
    """envelope-encrypt + decrypt via Cloud KMS (real project only).

    Runs only when an operator wires GCP_KMS_KEY_NAME against a real
    project + ADC. The named key MUST exist and the identity MUST hold
    ``roles/cloudkms.cryptoKeyEncrypterDecrypter`` on it.

    Steps:
      1. Re-import server.crypto with APP_KMS_BACKEND=gcp_kms.
      2. Round-trip a 32-byte data key through encrypt/decrypt.
      3. Assert plaintext is recovered and never appears in the wire blob.
      4. Tamper-reject: flip a byte and assert Cloud KMS raises a
         GoogleAPIError-family exception (InvalidArgument in practice).
    """
    from google.api_core.exceptions import GoogleAPIError

    reimport_crypto({
        "APP_KMS_BACKEND": "gcp_kms",
        "GCP_KMS_KEY_NAME": GCP_KMS_KEY_NAME,
    })
    from server.crypto import get_backend  # noqa: E402

    backend = get_backend()

    plaintext = b"\xef" * 32
    ciphertext = backend.encrypt(plaintext)

    assert isinstance(ciphertext, bytes), "encrypt must return bytes"
    assert len(ciphertext) > len(plaintext), \
        "Cloud KMS wrap should add metadata + auth tag"
    assert plaintext not in ciphertext, \
        "plaintext bytes must not appear inside the wrapped blob"

    decrypted = backend.decrypt(ciphertext)
    assert decrypted == plaintext, "round-trip must recover the data key exactly"

    # Tamper: flip a byte in the middle of the wrapped blob.
    tampered = bytearray(ciphertext)
    tampered[len(tampered) // 2] ^= 0xFF
    with pytest.raises(GoogleAPIError):
        backend.decrypt(bytes(tampered))


VAULT_ADDR = os.environ.get("VAULT_ADDR")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN")


@pytest.mark.secrets
@pytest.mark.slow
@pytest.mark.skipif(
    not (VAULT_ADDR and VAULT_TOKEN),
    reason="VAULT_ADDR + VAULT_TOKEN not set; Vault Transit test requires a Vault dev server",
)
@pytest.mark.skipif(not kms_backend_available("vault_transit"), reason="hvac not installed")
def test_vault_transit_roundtrip() -> None:
    """envelope-encrypt + decrypt via HashiCorp Vault Transit (dev-mode locally / CI).

    Steps:
      1. Bootstrap: mount the transit engine (idempotent — tolerates the
         "already mounted" case so this works against a long-lived dev
         container) and create the named key ``pfactory-test``.
      2. Re-import server.crypto with APP_KMS_BACKEND=vault_transit pointed
         at the dev server.
      3. Wrap a fresh 32-byte data key, then unwrap.
      4. Assert plaintext round-trips, the wire format starts with
         ``vault:v1:`` (key version 1 on a freshly-created key), and the
         plaintext bytes never appear inside the wrapped blob.
      5. Tamper-reject: flip a byte in the base64 ciphertext portion and
         expect hvac to raise (Vault returns 400 with "invalid ciphertext").
    """
    import hvac
    from hvac.exceptions import InvalidRequest

    bootstrap = hvac.Client(url=VAULT_ADDR, token=VAULT_TOKEN)
    # Mount transit. Idempotent: if it's already mounted (long-lived
    # dev container reused across runs), Vault returns 400 with
    # "path is already in use". We swallow that case only.
    try:
        bootstrap.sys.enable_secrets_engine(backend_type="transit", path="transit")
    except InvalidRequest as e:
        if "path is already in use" not in str(e):
            raise

    key_name = "pfactory-test"
    bootstrap.secrets.transit.create_key(name=key_name)

    reimport_crypto({
        "APP_KMS_BACKEND": "vault_transit",
        "VAULT_ADDR": VAULT_ADDR,
        "VAULT_TOKEN": VAULT_TOKEN,
        "VAULT_TRANSIT_KEY": key_name,
    })
    from server.crypto import get_backend  # noqa: E402

    backend = get_backend()

    plaintext = b"\xab" * 32
    ciphertext = backend.encrypt(plaintext)

    assert isinstance(ciphertext, bytes), "encrypt must return bytes"
    assert ciphertext.startswith(b"vault:v1:"), \
        f"expected vault:v1: prefix on a fresh key, got {ciphertext[:32]!r}"
    assert plaintext not in ciphertext, \
        "plaintext bytes must not appear inside the wrapped wire format"

    decrypted = backend.decrypt(ciphertext)
    assert decrypted == plaintext, "round-trip must recover the data key exactly"

    # Tamper: flip a byte in the base64 segment (after the version prefix).
    # Vault rejects with InvalidRequest (HTTP 400, "invalid ciphertext").
    tampered = bytearray(ciphertext)
    # Pick a byte well past the "vault:v1:" prefix.
    idx = len(b"vault:v1:") + 4
    tampered[idx] = ord("A") if tampered[idx] != ord("A") else ord("B")
    with pytest.raises((InvalidRequest, ValueError)):
        backend.decrypt(bytes(tampered))
