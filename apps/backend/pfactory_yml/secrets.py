"""
Environment-variable secret resolution helper.
===============================================

PFactory's ``.pfactory.yml`` config stores env-var **names** (e.g.
``token_env: STAGING_API_TOKEN``), never the raw secret values.  Callers
(the Executor in Task 8) resolve secrets at *runtime* by calling
``resolve_env_var()``.

This design (Decision 7) means the config file can be committed to the
AIFactory repo and shared in pull-request diffs without leaking credentials.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


class MissingSecretError(RuntimeError):
    """Raised when a required env-var is not set at runtime.

    Attributes
    ----------
    env_var_name:
        The name of the missing environment variable.
    """

    def __init__(self, env_var_name: str) -> None:
        self.env_var_name = env_var_name
        super().__init__(
            f"Required environment variable {env_var_name!r} is not set.  "
            "Set it before running the Executor, or check your CI/CD secrets "
            "configuration."
        )


def resolve_env_var(name: str) -> str:
    """Return the value of env-var *name*, raising :exc:`MissingSecretError`
    if it is not set.

    This function is intentionally NOT called by the parser — it exists only
    for use by the Executor (Task 8) and other runtime consumers.

    Parameters
    ----------
    name:
        Uppercase env-var name, e.g. ``"STAGING_API_TOKEN"``.

    Returns
    -------
    str
        The env-var value.

    Raises
    ------
    MissingSecretError
        If ``os.environ`` does not contain *name*.
    """
    value = os.environ.get(name)
    if value is None:
        raise MissingSecretError(name)
    return value


def resolve_auth_env_vars(auth: object) -> dict[str, str]:
    """Resolve all env-var references in an auth object at runtime.

    This is a convenience wrapper for the Executor to call once it has a
    concrete auth model instance.  It introspects the model's fields for any
    ``*_env`` suffixed strings, resolves them via :func:`resolve_env_var`,
    and returns a plain ``dict`` mapping the resolved NAMES to VALUES.

    Parameters
    ----------
    auth:
        Any auth model instance (e.g. ``BearerAuth``, ``BasicAuth``,
        ``OAuth2ClientCredentialsAuth``).  If the object has no ``*_env``
        fields the dict is empty.

    Returns
    -------
    dict[str, str]
        Mapping of env-var names to resolved values.

    Raises
    ------
    MissingSecretError
        If any referenced env var is not set.

    Examples
    --------
    ::

        auth = BearerAuth(type="bearer", token_env="STAGING_API_TOKEN")
        resolved = resolve_auth_env_vars(auth)
        # resolved == {"STAGING_API_TOKEN": "ghp_actual_value"}
    """
    resolved: dict[str, str] = {}

    # Pydantic models expose their fields via model_fields / __dict__.
    # We look for any str-valued attribute whose NAME ends in "_env".
    # ``auth`` is typed ``object`` (any auth model), so the attribute is read
    # with getattr rather than dotted access: same fallback as the previous
    # try/except AttributeError, but one mypy can see the type of.
    fields: Mapping[str, object] | None = getattr(auth, "model_fields", None)  # Pydantic v2
    if fields is None:
        return resolved

    for field_name in fields:
        if not field_name.endswith("_env"):
            continue
        env_var_name = getattr(auth, field_name, None)
        if isinstance(env_var_name, str):
            resolved[env_var_name] = resolve_env_var(env_var_name)

    return resolved
