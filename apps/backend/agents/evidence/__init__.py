"""Evidence capture package — Task 16 / #32.

Re-exports the public API surface so callers import from a single place::

    from agents.evidence import (
        evidence_dir_for_test,
        evidence_urls_for_test,
        record_http_to_har,
        enforce_retention,
    )
"""

from __future__ import annotations

from agents.evidence.http_recorder import record_http_to_har
from agents.evidence.layout import (
    content_type_for_artifact,
    evidence_dir_for_test,
    evidence_urls_for_test,
    render_auth_setup,
    render_playwright_config,
    scaffold_auth_setup,
)
from agents.evidence.retention import RetentionStats, enforce_retention

__all__ = [
    "RetentionStats",
    "content_type_for_artifact",
    "enforce_retention",
    "evidence_dir_for_test",
    "evidence_urls_for_test",
    "record_http_to_har",
    "render_auth_setup",
    "render_playwright_config",
    "scaffold_auth_setup",
]
