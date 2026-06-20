"""
AWS Secrets Manager backend (``aws-sm://<name>[#<json-field>]`` refs).

Uses ``boto3`` (lazily imported). Credentials/region come from the standard
boto3 chain (env keys, ``~/.aws/credentials``, profile, IRSA) — the same chain
``core.mcp_credentials`` probes. Region resolution order: ref ``extra['region']``
→ ``AWS_REGION`` → ``AWS_DEFAULT_REGION``. A ``#field`` selects a key from a
JSON-encoded secret. Egress is MANAGED_CLOUD.
"""

from __future__ import annotations

import json
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


class AwsSecretsManagerBackend(SecretsBackend):
    name = "aws_secrets_manager"

    def __init__(self, region: str | None = None) -> None:
        self._region = (
            region
            or os.environ.get("AWS_REGION", "").strip()
            or os.environ.get("AWS_DEFAULT_REGION", "").strip()
            or None
        )

    def available(self) -> bool:
        try:
            import boto3  # noqa: F401
        except ImportError:
            return False
        return True

    def egress_class(self) -> EgressClass:
        return EgressClass.MANAGED_CLOUD

    def resolve(self, ref: SecretRef) -> SecretValue:
        try:
            import boto3
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError as exc:
            raise BackendUnavailableError(
                "boto3 not installed — `pip install boto3` to use aws-sm: refs."
            ) from exc

        region = ref.extra.get("region") or self._region
        client = boto3.client("secretsmanager", region_name=region)
        try:
            resp = client.get_secret_value(SecretId=ref.locator)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("ResourceNotFoundException", "InvalidRequestException"):
                raise SecretNotFoundError(f"AWS secret {ref.locator!r} not found") from exc
            raise SecretsError(f"AWS get_secret_value failed: {exc}") from exc
        except BotoCoreError as exc:
            raise SecretsError(f"AWS get_secret_value failed: {exc}") from exc

        raw = resp.get("SecretString")
        if raw is None:
            raise SecretNotFoundError(f"AWS secret {ref.locator!r} has no SecretString (binary?)")

        value = _select(raw, ref.field, ref.locator)
        return SecretValue(
            value=value, backend=self.name, ref=ref.raw, source=f"aws-sm:{ref.locator}"
        )


def _select(raw: str, field: str | None, name: str) -> str:
    if field is None:
        return raw
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretsError(
            f"AWS secret {name!r} is not JSON, cannot select field {field!r}"
        ) from exc
    if not isinstance(obj, dict) or field not in obj:
        raise SecretNotFoundError(f"Field {field!r} not in AWS secret {name!r}")
    return str(obj[field])


__all__ = ["AwsSecretsManagerBackend"]
