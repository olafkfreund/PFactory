"""Allowlisted environment for agent and repository-code children.

The rules live in the shared ``agent_sandbox`` package (Factory#3709); this
module keeps PFactory's data as a Policy and the public names callers import.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import agent_sandbox
from agent_sandbox import Policy
from core.auth import SDK_ENV_VARS

CLAUDE_KEEP = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    *SDK_ENV_VARS,
}

GITHUB_KEEP: tuple[str, ...] = ("GITHUB_TOKEN", "GH_TOKEN")

_PREFIX = "PFACTORY"

POLICY = Policy(
    extra_env=f"{_PREFIX}_AGENT_ENV_EXTRA",
    deny_exact=frozenset(
        {
            "API_TOKEN",
            "APP_API_TOKEN",
            "JWT_SECRET",
            "APP_JWT_SECRET",
            "DATABASE_URL",
            "APP_DATABASE_URL",
            "AIFACTORY_TOKEN",
            "AIFACTORY_MARKER_NONCE",
            "APP_CFACTORY_READ_KEY",
            "S3_SECRET_KEY",
            "AIFACTORY_TRUSTED_CONTRACT",
            "NIX_CONFIG",
            "NIX_ACCESS_TOKENS",
            *(
                f"{_PREFIX}_{name}"
                for name in (
                    "MCP_KEY",
                    "API_TOKEN_FILE",
                    "AIFACTORY_API_TOKEN",
                    "BACKSTAGE_TOKEN",
                    "CONFLUENCE_TOKEN",
                    "CONTRACT_SIGNING_KEY",
                    "GITBOOK_TOKEN",
                    "MCP_SECRET",
                    "NOTION_TOKEN",
                    "RECON_TOKEN",
                )
            ),
        }
    ),
    deny_pattern=re.compile(
        r"SECRET|PASSWORD|PRIVATE_KEY|CREDENTIAL|(?:^|_)KMS|PASSPHRASE|TRUSTED_PLAN_KEY|SIGNING_KEY",
        re.I,
    ),
    allow_pattern=re.compile(
        r"PATH|LANG|LANGUAGE|TZ|TERM|COLORTERM|TMPDIR|USER|LOGNAME|SHELL|CI"
        r"|PYTHONUNBUFFERED|PYTHONIOENCODING|TRACEPARENT"
        r"|SSL_CERT_FILE|SSL_CERT_DIR|NODE_EXTRA_CA_CERTS|REQUESTS_CA_BUNDLE"
        r"|GIT_(?:AUTHOR|COMMITTER)_(?:NAME|EMAIL)"
        r"|GIT_CONFIG_(?:COUNT|KEY_\d+|VALUE_\d+)|GIT_PAGER"
        r"|OPENAI_BASE_URL|GOOGLE_CLOUD_PROJECT|GOOGLE_CLOUD_LOCATION|GOOGLE_GENAI_USE_VERTEXAI"
        r"|LC_\w+|CLAUDE_CODE_\w+|NIX_\w+|(?i:(?:HTTPS?|NO|ALL)_PROXY)"
        r"|PLAYWRIGHT_BROWSERS_PATH|PLAYWRIGHT_SKIP_\w+|FONTCONFIG_FILE"
        r"|HOME|XDG_\w+_HOME|CODEX_HOME"
    ),
    keep=frozenset(CLAUDE_KEEP),
    credential_blocks_allow=True,
)

pin_git_config = agent_sandbox.pin_git_config


def is_denied_env_key(name: str) -> bool:
    """True when ``name`` is a control-plane secret no child may see."""
    denied: bool = agent_sandbox.is_denied_env_key(POLICY, name)
    return denied


def is_agent_env_allowed(name: str) -> bool:
    """Runtime names, plus the operator's PFACTORY_AGENT_ENV_EXTRA; never a denied one."""
    allowed: bool = agent_sandbox.is_agent_env_allowed(POLICY, name)
    return allowed


def get_agent_env_blanks() -> dict[str, str]:
    """``{name: ""}`` for host variables the Claude CLI must not see.

    The SDK builds the CLI env as ``{**os.environ, **options.env}``, so a
    variable can only be neutralised by an empty override.
    """
    blanks: dict[str, str] = agent_sandbox.get_agent_env_blanks(POLICY)
    return blanks


def child_env(keep: Iterable[str] = (), extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Env for a child we spawn: allowed names, then ``keep``, then ``extra``.

    Carries no credential unless the caller names it in ``keep``.
    """
    env: dict[str, str] = agent_sandbox.child_env(POLICY, keep, extra, agent=True, pin_git=False)
    return env


def git_env(keep: Iterable[str] = (), extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """``child_env`` plus the git exec-bearing config pins, for every git child."""
    env: dict[str, str] = agent_sandbox.child_env(POLICY, keep, extra, agent=True, pin_git=True)
    return env


def ensure_baseline_dir() -> None:
    """Keep git metadata baselines under ``~/.pfactory``, which the service already writes."""
    if "FACTORY_GIT_BASELINE_DIR" not in os.environ:
        os.environ["FACTORY_GIT_BASELINE_DIR"] = str(Path.home() / ".pfactory" / "git-baselines")


def merge_driver_pins(
    cwd: str | os.PathLike[str],
    run: Callable[[list[str]], tuple[int, str]] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    baseline: bool = False,
) -> list[str]:
    """Empty ``-c`` pins for merge drivers and content filters (fails closed).

    Raises RuntimeError when git cannot list the config, or when a remote sets a
    program key. With ``baseline`` the git metadata baseline is verified first
    (Factory#3737); the web-server helpers on a user's own repo leave it off.
    """
    check: Callable[[str | os.PathLike[str], object], None] | None = None
    if baseline:
        ensure_baseline_dir()
        # Not the injected runner: a text-mode runner folds CR into LF and hides a change.

        def check(cwd: str | os.PathLike[str], _run: object) -> None:
            agent_sandbox.check_git_metadata(cwd)
    pins: list[str] = agent_sandbox.merge_driver_pins(
        cwd, run, env if env is not None else git_env(), check
    )
    return pins


def without_host_secrets(env: Mapping[str, str]) -> dict[str, str]:
    """Drop entries whose value is a denied host variable's value (a renamed copy)."""
    out: dict[str, str] = agent_sandbox.without_host_secrets(POLICY, env)
    return out
