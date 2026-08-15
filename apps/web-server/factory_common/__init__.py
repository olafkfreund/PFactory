"""factory-common: the deduped stdlib-only hub utility layer.

Single source of truth for cross-cutting primitives the Factory fleet repeatedly
re-implements:

* :mod:`factory_common.http` - Cloudflare-friendly typed urllib JSON client.
* :mod:`factory_common.secrets` - canonical secret-pattern table + redact/scan.
* :mod:`factory_common.logsafe` - CWE-117 log-value sanitizer (sanitize_log).
* :mod:`factory_common.url_safety` - SSRF guard for outbound URLs.

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
from factory_common.url_safety import (
    MAX_REDIRECT_HOPS,
    assert_safe_outbound_url,
    build_no_redirect_opener,
    fetch_following_safe_redirects,
)

__all__ = [
    "DEFAULT_MAX_LENGTH",
    "DEFAULT_USER_AGENT",
    "MAX_REDIRECT_HOPS",
    "PLACEHOLDER",
    "SECRET_PATTERNS",
    "HttpClient",
    "HttpResponse",
    "SecretPattern",
    "assert_safe_outbound_url",
    "basic_auth",
    "bearer_auth",
    "build_no_redirect_opener",
    "contains_secret",
    "fetch_following_safe_redirects",
    "is_success",
    "no_auth",
    "private_token_auth",
    "redact",
    "sanitize_log",
    "scan",
]
