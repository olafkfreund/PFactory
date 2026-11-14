"""Allowlisted environment for agent and repository-code children.

Port of AIFactory's rules (core/auth.py, core/child_env.py; #1680, #1757) with
the same names, so the copies can become one package (Factory#3709).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping

from core.auth import SDK_ENV_VARS

logger = logging.getLogger(__name__)

CLAUDE_KEEP = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    *SDK_ENV_VARS,
}

_AGENT_ENV_DENY_EXACT = {
    "API_TOKEN",
    "APP_API_TOKEN",
    "JWT_SECRET",
    "APP_JWT_SECRET",
    "DATABASE_URL",
    "APP_DATABASE_URL",
    "AIFACTORY_TOKEN",
    "AIFACTORY_MARKER_NONCE",
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
    "APP_CFACTORY_READ_KEY",
    "CONTEXT7_KEY",
    "RAPIDAPI_KEY",
    "LANGCHAIN_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "OLLAMA_API_KEY",
    "AIFACTORY_TRUSTED_CONTRACT",
    "NIX_CONFIG",
    "NIX_ACCESS_TOKENS",
    "PFACTORY_MCP_KEY",
    "PFACTORY_API_TOKEN_FILE",
    "PFACTORY_AIFACTORY_API_TOKEN",
    "PFACTORY_BACKSTAGE_TOKEN",
    "PFACTORY_CONFLUENCE_TOKEN",
    "PFACTORY_CONTRACT_SIGNING_KEY",
    "PFACTORY_GITBOOK_TOKEN",
    "PFACTORY_MCP_SECRET",
    "PFACTORY_NOTION_TOKEN",
    "PFACTORY_RECON_TOKEN",
}
_AGENT_ENV_DENY_PATTERN = re.compile(
    r"SECRET|PASSWORD|PRIVATE_KEY|CREDENTIAL|(?:^|_)KMS|PASSPHRASE|TRUSTED_PLAN_KEY|SIGNING_KEY",
    re.I,
)
_AGENT_ENV_ALLOW = re.compile(
    r"PATH|LANG|LANGUAGE|TZ|TERM|COLORTERM|TMPDIR|USER|LOGNAME|SHELL|CI"
    r"|PYTHONUNBUFFERED|PYTHONIOENCODING|TRACEPARENT"
    r"|SSL_CERT_FILE|SSL_CERT_DIR|NODE_EXTRA_CA_CERTS|REQUESTS_CA_BUNDLE"
    r"|GIT_(?:AUTHOR|COMMITTER)_(?:NAME|EMAIL)"
    r"|GIT_CONFIG_(?:COUNT|KEY_\d+|VALUE_\d+)|GIT_PAGER"
    r"|OPENAI_BASE_URL|GOOGLE_CLOUD_PROJECT|GOOGLE_CLOUD_LOCATION|GOOGLE_GENAI_USE_VERTEXAI"
    r"|LC_\w+|CLAUDE_CODE_\w+|NIX_\w+|(?i:(?:HTTPS?|NO|ALL)_PROXY)"
    r"|PLAYWRIGHT_BROWSERS_PATH|PLAYWRIGHT_SKIP_\w+|FONTCONFIG_FILE"
    r"|HOME|XDG_\w+_HOME|CODEX_HOME"
)
# Credential-shaped names the allow pattern lets through (CLAUDE_CODE_OAUTH_TOKEN,
# an opted-in MCP token). A child we spawn gets one only by naming it in ``keep``.
_CREDENTIAL_NAME = re.compile(r"(_KEY|_TOKEN)$", re.I)
_logged_dropped_env_names: set[str] = set()


def is_denied_env_key(name: str) -> bool:
    """True when ``name`` is a control-plane secret no child may see."""
    return name.upper() in _AGENT_ENV_DENY_EXACT or bool(_AGENT_ENV_DENY_PATTERN.search(name))


def is_agent_env_allowed(name: str) -> bool:
    """Runtime names, plus the operator's PFACTORY_AGENT_ENV_EXTRA; never a denied one."""
    extra = {item.strip() for item in os.environ.get("PFACTORY_AGENT_ENV_EXTRA", "").split(",")}
    return not is_denied_env_key(name) and (bool(_AGENT_ENV_ALLOW.fullmatch(name)) or name in extra)


def _log_dropped(names: Iterable[str]) -> None:
    new = {name for name in names if not is_denied_env_key(name)} - _logged_dropped_env_names
    if new:
        _logged_dropped_env_names.update(new)
        logger.info("agent env allowlist dropped (names only): %s", sorted(new))


def get_agent_env_blanks() -> dict[str, str]:
    """``{name: ""}`` for host variables the Claude CLI must not see.

    The SDK builds the CLI env as ``{**os.environ, **options.env}``, so a
    variable can only be neutralised by an empty override.
    """
    blanks = {
        name: ""
        for name in os.environ
        if name not in CLAUDE_KEEP and not is_agent_env_allowed(name)
    }
    _log_dropped(blanks)
    return blanks


def child_env(keep: Iterable[str] = (), extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Env for a child we spawn: allowed names, then ``keep``, then ``extra``.

    Carries no credential unless the caller names it in ``keep``.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if is_agent_env_allowed(name) and not _CREDENTIAL_NAME.search(name)
    }
    _log_dropped(name for name in os.environ if name not in env)
    env.update({name: os.environ[name] for name in keep if name in os.environ})
    if extra:
        env.update(extra)
    return env


def without_host_secrets(env: Mapping[str, str]) -> dict[str, str]:
    """Drop entries whose value is a denied host variable's value (a renamed copy)."""
    secrets = {value for name, value in os.environ.items() if value and is_denied_env_key(name)}
    return {name: value for name, value in env.items() if value not in secrets}
