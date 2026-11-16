"""Allowlisted environment for agent and repository-code children (Factory#3709).

One implementation of the rules AIFactory, PFactory and TFactory each carried:
a per-service ``Policy`` holds the data, the functions here hold the logic.
Stdlib only: services vendor this package byte for byte.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping

from .git_pins import pin_git_config
from .policy import Policy

logger = logging.getLogger(__name__)

# Host secrets no service may let a child see. Services add their own names.
BASE_DENY_EXACT: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "OPENROUTER_API_KEY",
        "VOYAGE_API_KEY",
        "MISTRAL_API_KEY",
        "DEEPSEEK_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_CLIENT_SECRET",
        "VAULT_TOKEN",
        "LINEAR_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "CONTEXT7_KEY",
        "RAPIDAPI_KEY",
        "LANGCHAIN_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
        "S3_ACCESS_KEY",
        "OLLAMA_API_KEY",
        # Factory#3709: control-plane and VCS credentials the agent never needs.
        "CFACTORY_READ_KEY",
        "CFACTORY_GITHUB_TOKEN",
        "GITLAB_TOKEN",
        "GL_TOKEN",  # glab
    }
)

CREDENTIAL_NAME = re.compile(r"(_KEY|_TOKEN)(_FILE)?$", re.IGNORECASE)

# Names whose blank value breaks the tool (git: "not a git repository") while the
# SDK can only blank, never unset. Path redirections, not secrets (Factory#3720).
BLANK_BREAKS: frozenset[str] = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
    }
)

_PROXY_NAME = re.compile(r"(?:HTTPS?|ALL|FTP)_PROXY", re.IGNORECASE)
# Everything up to the last "@" before the first "/", after an optional scheme.
_USERINFO = re.compile(r"^((?:[A-Za-z][A-Za-z0-9+.-]*://)?)[^/]*@")

# shortcut: a shorter value ("true", "1") is not treated as a secret copy by
# without_host_secrets; raise it if a real secret can be that short.
_MIN_SECRET_LEN = 8

_logged_dropped: set[str] = set()


def strip_proxy_userinfo(value: str) -> str | None:
    """``value`` without ``user:password@``; None when an ``@`` is still left."""
    out = _USERINFO.sub(r"\1", value)
    return None if "@" in out else out


def is_denied_env_key(policy: Policy, name: str) -> bool:
    """True when ``name`` is a host secret that must not reach a child process."""
    return (
        name.upper() in BASE_DENY_EXACT
        or name.upper() in policy.deny_exact
        or bool(policy.deny_pattern.search(name))
    )


def is_agent_env_allowed(policy: Policy, name: str) -> bool:
    """Allow only the agent's auth, runtime and explicitly named variables."""
    if policy.keep_is_allowed and name in policy.keep:
        return True
    extra = {n.strip() for n in os.environ.get(policy.extra_env, "").split(",")}
    by_pattern = bool(policy.allow_pattern.fullmatch(name)) and not (
        policy.credential_blocks_allow and CREDENTIAL_NAME.search(name)
    )
    return (by_pattern or name in extra) and not is_denied_env_key(policy, name)


def _log_dropped(policy: Policy, names: Iterable[str]) -> None:
    """Log each newly dropped non-secret variable name once per process."""
    new = {n for n in names if not is_denied_env_key(policy, n)} - _logged_dropped
    if new:
        _logged_dropped.update(new)
        logger.info("agent env allowlist dropped (names only): %s", sorted(new))


def get_agent_env_blanks(policy: Policy) -> dict[str, str]:
    """Return ``{var: ""}`` for host variables outside the agent allowlist.

    Blanking (not deleting) because the SDK merges ``options.env`` over the
    inherited environment. Never blanks ``policy.keep``, ``blank_exempt`` or
    ``BLANK_BREAKS``. An allowed proxy variable that carries ``user:password@`` gets
    the stripped value (blank when it cannot be stripped).
    """
    blanks = {
        k: ""
        for k in os.environ
        if k not in policy.keep
        and k.upper() not in BLANK_BREAKS
        and not (policy.blank_exempt and policy.blank_exempt(k))
        and not is_agent_env_allowed(policy, k)
    }
    _log_dropped(policy, blanks)
    for k, v in os.environ.items():
        # Allowed proxy names only: a denied or unapproved one is already blank.
        if _PROXY_NAME.fullmatch(k) and k not in blanks and is_agent_env_allowed(policy, k):
            stripped = strip_proxy_userinfo(v)
            if stripped != v:
                blanks[k] = stripped or ""
    return blanks


def _agent_value(name: str, value: str) -> str:
    """Inherited value for an agent child: proxy userinfo removed."""
    stripped = strip_proxy_userinfo(value) if _PROXY_NAME.fullmatch(name) else value
    return value if stripped is None else stripped  # None: dropped by the caller


def child_env(  # noqa: PLR0913 - one entry point, keyword flags
    policy: Policy,
    keep: Iterable[str] = (),
    extra: Mapping[str, str] | None = None,
    *,
    runner: bool = False,
    agent: bool = False,
    pin_git: bool = True,
) -> dict[str, str]:
    """Return a scrubbed copy of ``os.environ`` for a child process.

    Drops denied names and ``policy.strip_vars``; unless ``runner``, every
    ``*_KEY``/``*_TOKEN`` name; with ``agent``, every name outside the allowlist and
    userinfo (``user:password@``) from proxy variables.
    Then restores the ``keep`` names that are set, applies ``extra`` and, with
    ``pin_git``, appends the git exec-bearing config pins (they win over any
    ``GIT_CONFIG_*`` already present). Build it per call: ``os.environ`` changes.
    """
    env = {
        k: _agent_value(k, v) if agent else v
        for k, v in os.environ.items()
        if not is_denied_env_key(policy, k)
        # Linux env names are case-sensitive: a lowercase copy is its own variable.
        and k.upper() not in policy.strip_vars
        and (runner or not CREDENTIAL_NAME.search(k))
        and (not agent or is_agent_env_allowed(policy, k))
        and not (agent and _PROXY_NAME.fullmatch(k) and strip_proxy_userinfo(v) is None)
    }
    if agent:
        _log_dropped(policy, (k for k in os.environ if k not in env))
    env.update({k: os.environ[k] for k in keep if k in os.environ})
    if extra:
        env.update(extra)
    return pin_git_config(env) if pin_git else env


def without_host_secrets(policy: Policy, env: Mapping[str, str]) -> dict[str, str]:
    """Drop entries whose value is a denied host variable's value (a renamed copy)."""
    secrets = {
        v
        for k, v in os.environ.items()
        if len(v) >= _MIN_SECRET_LEN and is_denied_env_key(policy, k)
    }
    return {k: v for k, v in env.items() if v not in secrets}
