"""Operator-level credential config — ``~/.pfactory/credentials.json`` (#71).

Formalises the schema the :class:`CredentialBroker` reads, extending the
``~/.pfactory/mcp-credentials.json`` posture (0600, *references* not secrets):

```json
{
  "cloud": {
    "gcp": { "ref": "gcp-sm://proj/sa-key", "as": "GOOGLE_APPLICATION_CREDENTIALS", "kind": "file" }
  },
  "credentials": {
    "staging-db": { "ref": "vault:secret/data/staging/db#url", "as": "DATABASE_URL" }
  }
}
```

- ``cloud`` — provider (``gcp``/``aws``/``azure``/``kubernetes``) → backend ref;
  the broker's "fetch from a vault" head (see ``broker._cloud_config``).
- ``credentials`` — named sets → backend ref, mirroring the per-project
  ``.pfactory.yml`` ``credentials:`` block so an operator can define creds once
  for every project on the host.

A value is never a secret itself — only a *reference* (``vault:…`` · ``gcp-sm://…``
· ``aws-sm://…`` · ``azurekv://…`` · ``sops:…`` · ``env:…``) the backends resolve
at run time. The file should be ``0600``; looser modes are loaded with a warning,
mirroring ``core.mcp_credentials``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

#: Canonical operator-config location. The broker re-exports this.
OPERATOR_CONFIG_PATH = Path.home() / ".pfactory" / "credentials.json"


class OperatorCredentialEntry(BaseModel):
    """One operator credential: a backend ref + how to expose it.

    Mirrors ``pfactory_yml.schema.CredentialEntry``: ``ref`` is a secret
    reference, ``as`` is the env-var name to set, ``kind=file`` writes the
    resolved value to a 0600 file and sets the env var to that path.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    ref: str
    as_: str | None = Field(default=None, alias="as")
    kind: str = "env"


class OperatorWifEntry(BaseModel):
    """Workload-identity-federation config for one cloud provider (#74).

    Mints **short-lived** scoped credentials from an OIDC token instead of a
    long-lived secret. AWS: ``role_arn`` + an OIDC token from ``token_file``
    (or inline ``token``) → STS ``AssumeRoleWithWebIdentity``. ``duration_seconds``
    bounds the session TTL; ``audience``/``session_name`` are optional.
    """

    model_config = ConfigDict(extra="forbid")

    role_arn: str | None = None
    token_file: str | None = None
    token: str | None = None
    audience: str | None = None
    session_name: str = "pfactory"
    duration_seconds: int = 3600


class OperatorCredentialsConfig(BaseModel):
    """Validated view of ``~/.pfactory/credentials.json``."""

    model_config = ConfigDict(extra="ignore")

    cloud: dict[str, OperatorCredentialEntry] = Field(default_factory=dict)
    credentials: dict[str, OperatorCredentialEntry] = Field(default_factory=dict)
    wif: dict[str, OperatorWifEntry] = Field(default_factory=dict)


def load_operator_config(path: Path | None = None) -> OperatorCredentialsConfig:
    """Load + validate the operator credential config.

    Returns an empty config when the file is absent, unreadable, not an object,
    or fails validation — credential config must never crash a run. Warns (but
    still loads) when the file is group/world-accessible.
    """
    p = path or OPERATOR_CONFIG_PATH
    if not p.exists():
        return OperatorCredentialsConfig()
    try:
        mode = p.stat().st_mode & 0o777
        if mode & 0o077:
            logger.warning(
                "%s is group/world-accessible (mode %o); recommend chmod 600.",
                p,
                mode,
            )
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            logger.warning("%s is not a JSON object; ignoring.", p)
            return OperatorCredentialsConfig()
        return OperatorCredentialsConfig.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Failed to read %s: %s", p, exc)
        return OperatorCredentialsConfig()


__all__ = [
    "OPERATOR_CONFIG_PATH",
    "OperatorCredentialEntry",
    "OperatorCredentialsConfig",
    "OperatorWifEntry",
    "load_operator_config",
]
