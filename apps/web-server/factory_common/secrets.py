"""Canonical secret-pattern table + redaction/scanning for the Factory fleet.

Every factory shells out to authenticated APIs (GitHub/GitLab/Azure DevOps, the
PARR services) and every factory logs. The June-2026 audit found the *same*
credential-leak class in more than one repo: a token printed inline in argv, in
an error string, in a log line (e.g. AIFactory#599, TFactory's now-fixed PAT
leak). The fix each repo reached for was an ad-hoc regex, so the pattern table
itself was about to be duplicated four ways.

This module is the single source of truth for "what a secret looks like" and the
two operations on it:

* :func:`scan` - find secret-shaped substrings in text (for a leak gate).
* :func:`redact` - replace them with a stable placeholder (for safe logging).

It is **stdlib-only** so it can be imported anywhere (CI, a coder pod, a script)
with no third-party dependency, exactly like the rest of the deduped hub layer.

The table is deliberately conservative: it targets credential *shapes* with low
false-positive rates (provider-prefixed tokens, AWS access-key ids, PEM private
keys, `Authorization:`/`PRIVATE-TOKEN:` header values, URL userinfo). It is not a
general entropy scanner - that is a separate, noisier tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern

PLACEHOLDER = "***REDACTED***"


@dataclass(frozen=True)
class SecretPattern:
    """One credential shape.

    ``regex`` matches the secret. If a capture group named ``secret`` is present
    only that group is redacted (so surrounding context such as a header name or
    URL scheme survives); otherwise the whole match is redacted.
    """

    name: str
    regex: Pattern[str]
    description: str

    def redacted(self, match: re.Match[str]) -> str:
        """Return *match* with its secret span replaced by the placeholder."""
        groups = match.groupdict()
        if "secret" in groups and groups["secret"] is not None:
            whole = match.group(0)
            secret = groups["secret"]
            start = match.start("secret") - match.start(0)
            end = start + len(secret)
            return whole[:start] + PLACEHOLDER + whole[end:]
        return PLACEHOLDER


def _p(name: str, pattern: str, description: str, flags: int = 0) -> SecretPattern:
    return SecretPattern(name=name, regex=re.compile(pattern, flags), description=description)


# Canonical pattern table (single source of truth for the fleet).
#
# Ordering matters only for reporting; redaction applies every pattern. Patterns
# that wrap a credential in context (headers, URL userinfo) use a named ``secret``
# group so the context is preserved in redacted output.
SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    _p(
        "github-pat",
        r"gh[pousr]_[A-Za-z0-9]{36,255}",
        "GitHub personal-access / OAuth / refresh / server / user token (ghp_, gho_, ...).",
    ),
    _p(
        "github-fine-grained-pat",
        r"github_pat_[A-Za-z0-9_]{22,255}",
        "GitHub fine-grained personal-access token (github_pat_...).",
    ),
    _p(
        "gitlab-pat",
        r"glpat-[A-Za-z0-9_-]{20,}",
        "GitLab personal-access token (glpat-...).",
    ),
    _p(
        "slack-token",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        "Slack bot/user/app token (xoxb-, xoxp-, ...).",
    ),
    _p(
        "aws-access-key-id",
        r"(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}",
        "AWS access key id.",
    ),
    _p(
        "private-key-block",
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
        "PEM/OpenSSH private-key header.",
    ),
    _p(
        "authorization-bearer",
        r"(?i:authorization)\s*[:=]\s*(?i:bearer)\s+(?P<secret>[A-Za-z0-9._~+/=-]{8,})",
        "HTTP Authorization: Bearer <token> header value.",
    ),
    _p(
        "authorization-basic",
        r"(?i:authorization)\s*[:=]\s*(?i:basic)\s+(?P<secret>[A-Za-z0-9+/=]{8,})",
        "HTTP Authorization: Basic <base64> header value.",
    ),
    _p(
        "private-token-header",
        r"(?i:private-token)\s*[:=]\s*(?P<secret>[A-Za-z0-9._-]{8,})",
        "GitLab PRIVATE-TOKEN header value.",
    ),
    _p(
        "url-userinfo",
        r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:@/\s]+):(?P<secret>[^@/\s]+)@",
        "Credential embedded in a URL (scheme://user:secret@host).",
    ),
)


def scan(text: str) -> list[tuple[str, str]]:
    """Return ``(pattern_name, matched_text)`` for every secret-shaped span.

    The matched text is the raw secret span (the named ``secret`` group when
    present, else the whole match) - callers wanting to *display* findings should
    redact first. This is the primitive a leak gate builds on.
    """
    findings: list[tuple[str, str]] = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.regex.finditer(text):
            groups = match.groupdict()
            span = groups["secret"] if groups.get("secret") is not None else match.group(0)
            findings.append((pattern.name, span))
    return findings


def redact(text: str) -> str:
    """Return *text* with every recognised secret replaced by the placeholder.

    Safe to wrap any log line / error string / argv with. Context around a
    credential (header names, URL scheme + user) is preserved; only the secret
    span is replaced.
    """
    result = text
    for pattern in SECRET_PATTERNS:
        result = pattern.regex.sub(pattern.redacted, result)
    return result


def contains_secret(text: str) -> bool:
    """True if *text* contains at least one recognised secret shape."""
    return any(pattern.regex.search(text) for pattern in SECRET_PATTERNS)
