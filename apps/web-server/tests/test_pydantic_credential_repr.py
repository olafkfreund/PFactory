"""Guard: no credential-bearing pydantic field may render its secret.

Factory#377, the pydantic half of Factory#372. Request models across
``server/routes`` declared their secret as a plain ``str``, so pydantic rendered
it verbatim on every surface::

    >>> repr(CreateGitCredentialRequest(token="glpat-SUPERSECRET123", ...))
    "CreateGitCredentialRequest(..., token='glpat-SUPERSECRET123', ...)"

A FastAPI request model is not a debug-only surface: it is the object bound to a
local in the traceback frame when the handler's downstream call raises, so an
unhandled exception writes the credential into the log, the error sink and any
crash reporter attached to them. The irony was sharpest in
``routes/git_credentials.py``, where the field's own ``description`` reads "The
Personal Access Token. Never logged, encrypted at rest" -- untrue for as long as
the model was reprable.

Two guards, mirroring ``tests/test_provider_credential_repr.py`` in the hub:

* :func:`test_no_credential_field_renders_its_secret` is the generic one. It
  parses every module under the web server and the two credential-handling
  backend packages with :mod:`ast` -- no imports, no third-party dependency --
  and fails any credential-named field on a ``BaseModel``/``BaseSettings`` that
  is neither ``SecretStr`` nor ``repr=False``. A model added next month is
  covered without anyone remembering this file exists. Pinning today's models by
  name would pass forever while the next ``token: str`` sailed in.
* The behavioural tests below construct the real models with a fake secret and
  prove it is absent from ``repr()``, ``str()`` and ``model_dump()`` -- and that
  the true value still reaches the point of use. A credential masked everywhere
  including where it is needed is a broken feature, not a secure one.

WHICH FIX APPLIES WHERE
    ``SecretStr`` is preferred, and is what the inbound request models use: it
    masks ``repr()``, ``str()``, ``model_dump()`` and ``model_dump_json()``
    alike, where ``repr=False`` masks only the first two and still leaks through
    a ``model_dump()`` that lands in a log line.

    ``repr=False`` is used only where ``SecretStr`` would break the feature, and
    each case is a real one here rather than a matter of taste:

    * ``AppSettings`` / ``SettingsUpdate`` / ``ClaudeProfile`` /
      ``ApiProfileUpdate`` / ``SourceEnvUpdate`` / ``ProjectEnvUpdate`` /
      ``ProjectSettings*`` / ``AutoFixConfigPayload`` are PERSISTED by dumping
      them -- ``save_app_settings`` writes ``model_dump_json()`` straight to
      disk. Under ``SecretStr`` the first save would overwrite every stored
      credential with ``"**********"``: silent, permanent credential loss.
    * ``AuthResponse`` / ``TokenResponse`` / ``RefreshResponse`` are RESPONSE
      models. The token has to reach the browser as JSON, and ``SecretStr``
      would serialise a mask, breaking login outright.
    * ``config.Settings`` interpolates its tokens directly
      (``f"Bearer {settings.API_TOKEN}"``), which under ``SecretStr`` still
      compiles while silently sending ``Bearer **********``.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).resolve().parents[1]
_REPO_ROOT = _WEB_SERVER.parents[1]

sys.path.insert(0, str(_WEB_SERVER))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "backend"))

from pfactory_secrets.operator_config import OperatorWifEntry  # noqa: E402
from server.config import Settings  # noqa: E402
from server.routes.auth_routes import (  # noqa: E402
    AuthResponse,
    LoginRequest,
    RegisterRequest,
)
from server.routes.git_credentials import CreateGitCredentialRequest  # noqa: E402
from server.routes.settings import AppSettings  # noqa: E402
from server.routes.test_target_credentials import (  # noqa: E402
    CreateTestCredentialRequest,
)

# Every tree that handles credentials. `apps/backend` as a whole is deliberately
# NOT scanned: it contains a checked-in `.venv-ci`, and walking third-party
# packages would bury real findings under hundreds of vendored SDK models.
_SCAN_ROOTS = (
    _WEB_SERVER / "server",
    _REPO_ROOT / "apps" / "backend" / "pfactory_secrets",
    _REPO_ROOT / "apps" / "backend" / "pfactory_yml",
)

# Words that name a secret. Checked against the FINAL name segment, because the
# last segment is what the field actually holds: `github_token` is a token, but
# `token_file` is a path, `token_env` is an environment variable's name and
# `api_key_preview` is a deliberately masked prefix. Matching anywhere in the
# name flags all three and trains people to suppress the guard, which is worse
# than not having one.
_SECRET_WORDS = frozenset(
    {
        "apikey",
        "credential",
        "credentials",
        "passphrase",
        "passwd",
        "password",
        "pat",
        "secret",
        "secrets",
        "token",
    }
)

# `key` alone is a lookup key (`correlation_key`, `idempotency_key`), so it
# counts only when the preceding segment makes it a credential.
_KEY_QUALIFIERS = frozenset(
    {
        "access",
        "api",
        "credential",
        "encryption",
        "master",
        "private",
        "secret",
        "signing",
        "ssh",
    }
)

# Verified false positives, kept explicit rather than by weakening the rule
# above -- a reviewer can see exactly what was exempted and why.
#
# `TestCredentialEntry.as_secret` is the NAME OF AN ENV VAR, not a secret. The
# `.pfactory.yml` block reads `{ref: store:42, as_secret: API_TOKEN}`, and
# `tools/runners/sandbox_credentials.py` does
# `env[spec.as_secret] = broker.resolve_ref(spec.ref).value` -- the field is the
# dict key, the credential is what `ref` resolves to. `schema.py` even validates
# it with `_check_env_names`. Masking it would render the config unusable while
# protecting nothing.
_NOT_SECRETS = frozenset({"TestCredentialEntry.as_secret"})

_MODEL_BASES = frozenset({"BaseModel", "BaseSettings"})

# Field names here are camelCase as often as snake_case (`githubToken`,
# `apiKey`), and a snake_case-only split silently misses every one of them --
# which is how the original issue's grep under-counted this surface threefold.
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z0-9]+")


def _segments(name: str) -> list[str]:
    """Split on underscores AND camelCase: ``githubToken`` -> ``[github, token]``."""
    out: list[str] = []
    for chunk in name.strip("_").split("_"):
        out += [w.lower() for w in _WORD.findall(chunk)]
    return out


def _is_secret_name(name: str) -> bool:
    segments = _segments(name)
    if not segments:
        return False
    if segments[-1] in _SECRET_WORDS:
        return True
    return segments[-1] in {"key", "keys"} and len(segments) > 1 and segments[-2] in _KEY_QUALIFIERS


def _is_str_annotation(node: ast.expr) -> bool:
    """True for ``str``/``SecretStr`` and unions thereof, false for containers.

    ``credentials: dict[str, Entry]`` mentions ``str`` but holds no secret of its
    own -- the secret, if any, lives on ``Entry`` and is guarded there.
    """
    text = ast.unparse(node)
    if any(c in text for c in ("dict[", "Dict[", "list[", "List[", "Mapping[")):
        return False
    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
    return bool(names & {"str", "SecretStr"})


def _has_repr_false(default: ast.expr | None) -> bool:
    """True when the assigned default is ``Field(..., repr=False)``."""
    if not isinstance(default, ast.Call):
        return False
    func = default.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name != "Field":
        return False
    return any(
        kw.arg == "repr" and isinstance(kw.value, ast.Constant) and kw.value.value is False
        for kw in default.keywords
    )


def _is_pydantic_model(node: ast.ClassDef) -> bool:
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if name in _MODEL_BASES:
            return True
    return False


def _modules() -> list[Path]:
    out: list[Path] = []
    for root in _SCAN_ROOTS:
        out += [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(out)


def test_scan_roots_are_present() -> None:
    """Fail loudly rather than passing vacuously if a tree moves."""
    for root in _SCAN_ROOTS:
        assert root.is_dir(), f"scan root missing: {root}"
    assert _modules()


def test_no_credential_field_renders_its_secret() -> None:
    """Every credential-named pydantic field is ``SecretStr`` or ``repr=False``."""
    offenders: list[str] = []

    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_pydantic_model(node):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                    continue
                field = stmt.target.id
                if f"{node.name}.{field}" in _NOT_SECRETS:
                    continue
                if not _is_secret_name(field) or not _is_str_annotation(stmt.annotation):
                    continue
                if "SecretStr" in ast.unparse(stmt.annotation) or _has_repr_false(stmt.value):
                    continue
                offenders.append(
                    f"{module.relative_to(_REPO_ROOT)}:{stmt.lineno} {node.name}.{field} "
                    f"-- credential renders in repr()/model_dump(); annotate it "
                    f"`SecretStr`, or `Field(..., repr=False)` if the value must "
                    f"stay a plain str at the point of use"
                )

    assert not offenders, "credential-bearing pydantic fields leak their secret:\n  " + "\n  ".join(
        offenders
    )


def test_git_credential_request_masks_the_pat() -> None:
    """The model whose own description promises the PAT is "never logged"."""
    secret = "glpat-SUPERSECRET123"  # noqa: S105 - fake, this is the leak probe
    body = CreateGitCredentialRequest(org_id="o1", name="ci", provider="gitlab", token=secret)

    assert secret not in repr(body)
    assert secret not in str(body)
    assert secret not in str(body.model_dump())
    assert secret not in body.model_dump_json()
    # ...and the handler still stores the real thing.
    assert body.token.get_secret_value() == secret


def test_test_target_credential_masks_its_secret() -> None:
    secret = "tt-SUPERSECRET321"  # noqa: S105 - fake, this is the leak probe
    body = CreateTestCredentialRequest(org_id="o1", name="staging-login", secret=secret)

    assert secret not in repr(body)
    assert secret not in str(body)
    assert secret not in str(body.model_dump())
    assert body.secret.get_secret_value() == secret


def test_login_and_register_mask_the_password() -> None:
    secret = "hunter2-SUPERSECRET"  # noqa: S105 - fake, this is the leak probe
    login = LoginRequest(email="a@b.com", password=secret)
    register = RegisterRequest(email="a@b.com", name="A", password=secret)

    for model in (login, register):
        assert secret not in repr(model)
        assert secret not in str(model)
        assert secret not in str(model.model_dump())
    # The password hasher and verifier must still see the real password.
    assert login.password.get_secret_value() == secret
    assert register.password.get_secret_value() == secret


def test_wif_entry_masks_its_oidc_token_but_still_mints() -> None:
    """The OIDC token handed to STS ``AssumeRoleWithWebIdentity``.

    ``wif._read_oidc_token`` reads this via ``getattr``, which no type checker
    can see through -- so this test is the only thing standing between a
    ``SecretStr`` field and STS being handed the literal string ``**********``.
    """
    from pfactory_secrets.wif import _read_oidc_token

    secret = "oidc-SUPERSECRET654"  # noqa: S105 - fake, this is the leak probe
    entry = OperatorWifEntry(role_arn="arn:aws:iam::1:role/r", token=secret)

    assert secret not in repr(entry)
    assert secret not in str(entry)
    assert secret not in str(entry.model_dump())
    assert _read_oidc_token(entry) == secret


def test_auth_response_hides_tokens_from_repr_but_still_serialises_them() -> None:
    """The response case: masked in logs, intact on the wire.

    ``AuthResponse`` uses ``repr=False`` rather than ``SecretStr`` precisely
    because the browser needs the real token. Masking ``model_dump()`` here
    would not be "more secure", it would be a broken login.
    """
    secret = "eyJ-SUPERSECRET-ACCESS"  # noqa: S105 - fake, this is the leak probe
    payload = {
        "id": "u1",
        "email": "a@b.com",
        "name": "A",
        "avatar_url": None,
        "role": "user",
        "is_active": True,
        "created_at": "2026-01-01T00:00:00",
    }
    response = AuthResponse(user=payload, access_token=secret, refresh_token=secret)

    assert secret not in repr(response)
    assert secret not in str(response)
    assert response.model_dump()["access_token"] == secret


def test_app_settings_hides_secrets_from_repr_but_still_persists_them() -> None:
    """The persisted case: ``save_app_settings`` dumps this straight to disk."""
    secret = "sk-ant-SUPERSECRET789"  # noqa: S105 - fake, this is the leak probe
    settings = AppSettings(globalAnthropicApiKey=secret, globalClaudeOAuthToken=secret)

    assert secret not in repr(settings)
    assert secret not in str(settings)
    # Under SecretStr this would round-trip as "**********" and destroy the
    # stored credential on the next save. That is why this model uses repr=False.
    assert settings.model_dump()["globalAnthropicApiKey"] == secret


def test_settings_does_not_render_its_credentials() -> None:
    """config.Settings is a process singleton in every configuration frame."""
    secret = "api-SUPERSECRET456"  # noqa: S105 - fake, this is the leak probe
    settings = Settings(API_TOKEN=secret, JWT_SECRET=secret)

    assert secret not in repr(settings)
    assert secret not in str(settings)
    # repr=False hides the field from rendering only; the value must survive or
    # every `Authorization: Bearer` header would be built from nothing.
    assert settings.API_TOKEN == secret
    assert settings.JWT_SECRET == secret
