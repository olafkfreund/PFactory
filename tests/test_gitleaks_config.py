"""`.gitleaks.toml` must use the singular `[allowlist]`.

gitleaks 8.24.3 -- the version gitleaks-action v3.0.0 installs -- does not
understand a top-level `[[allowlists]]` array. It parses the file without
complaint and applies NO allowlist at all, so every reviewed entry becomes
inert while the config still looks correct in review.

That is not hypothetical. Measured on this repo at the commit that added these
tests: `gitleaks detect --source . --config .gitleaks.toml --no-git` reported
1 finding not covered by the allowlist, now 0.

It stayed invisible for the usual reason -- neither observer could see it. The
scheduled full-history job failed on all three runs it had ever had and blocked
nothing, because a scheduled gate has no PR to turn red; the PR job only fails
when a diff happens to touch an allowlisted path, so it passed 23 times.

These tests need no gitleaks binary: they assert the shape of the config, which
is the part that silently regresses. Whether the allowlist actually suppresses
is asserted by CI running gitleaks itself.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / ".gitleaks.toml"


def _config() -> dict:
    with CONFIG.open("rb") as fh:
        return tomllib.load(fh)


def test_config_exists() -> None:
    assert CONFIG.is_file(), f"{CONFIG} is missing"


def test_uses_singular_allowlist_key() -> None:
    cfg = _config()
    assert "allowlists" not in cfg, (
        "`[[allowlists]]` (plural) is silently ignored by gitleaks 8.24.3 -- "
        "the whole allowlist becomes inert. Merge entries into the single "
        "`[allowlist]` block instead."
    )
    assert "allowlist" in cfg, "no `[allowlist]` block found"


def test_raw_text_has_no_plural_block() -> None:
    """Catch the literal header, not the word.

    tomllib reports only the LAST `[[allowlists]]` table under one key, so a
    shape check alone can miss a stray block. Match a real TOML header -- the
    header anchored at the start of a line -- rather than the substring, or the
    prose in the config explaining this very hazard trips the test.
    """
    header = re.compile(r"^\s*\[\[allowlists\]\]", re.MULTILINE)
    text = CONFIG.read_text(encoding="utf-8")
    assert not header.search(text), (
        "found a `[[allowlists]]` block header -- gitleaks 8.24.3 ignores it, "
        "which silently disables the entire allowlist. Merge it into the "
        "single `[allowlist]`."
    )


def test_allowlist_paths_are_valid_regexes() -> None:
    for pattern in _config()["allowlist"].get("paths", []):
        re.compile(pattern)  # raises re.error on a malformed entry


def test_known_fixture_files_are_covered() -> None:
    """The security-feature tests carry credential-shaped fixtures by design."""
    patterns = [re.compile(p) for p in _config()["allowlist"].get("paths", [])]
    for path in (
        "tests/test_scan_secrets.py",
        "tests/test_secrets_egress.py",
        "tests/test_security_scanner.py",
    ):
        assert any(p.search(path) for p in patterns), f"{path} is not allowlisted"


def test_allowlist_does_not_cover_application_code() -> None:
    """An allowlist wide enough to hide real code is worse than none."""
    patterns = [re.compile(p) for p in _config()["allowlist"].get("paths", [])]
    for path in (
        "apps/backend/main.py",
        "apps/web-server/server/main.py",
        "charts/aifactory/values.yaml",
    ):
        assert not any(p.search(path) for p in patterns), (
            f"{path} is allowlisted -- a real secret committed there would be "
            "suppressed"
        )
