"""factory-common: the deduped stdlib-only hub utility layer.

Single source of truth for cross-cutting primitives the Factory fleet repeatedly
re-implements:

* :mod:`factory_common.http` - Cloudflare-friendly typed urllib JSON client.
* :mod:`factory_common.secrets` - canonical secret-pattern table + redact/scan.
* :mod:`factory_common.logsafe` - CWE-117 log-value sanitizer (sanitize_log).

Established by the Phase-1 deduplication work in the code-quality program
(epic Factory#154, issue Factory#161).
"""

from __future__ import annotations

from factory_common.http import (
    DEFAULT_USER_AGENT,
    HttpClient,
    HttpResponse,
    basic_auth,
    bearer_auth,
    is_success,
    no_auth,
    private_token_auth,
)
from factory_common.logsafe import DEFAULT_MAX_LENGTH, sanitize_log
from factory_common.secrets import (
    PLACEHOLDER,
    SECRET_PATTERNS,
    SecretPattern,
    contains_secret,
    redact,
    scan,
)

__all__ = [
    "DEFAULT_MAX_LENGTH",
    "DEFAULT_USER_AGENT",
    "PLACEHOLDER",
    "SECRET_PATTERNS",
    "HttpClient",
    "HttpResponse",
    "SecretPattern",
    "basic_auth",
    "bearer_auth",
    "contains_secret",
    "is_success",
    "no_auth",
    "private_token_auth",
    "redact",
    "sanitize_log",
    "scan",
]
