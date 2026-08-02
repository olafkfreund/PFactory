#!/usr/bin/env python3
"""The lint ratchet applies the test assert bar to tests, and only to tests.

Factory#510. The ratchet linted a temp COPY of each changed file, and ruff
relativises a path against the project root before matching per-file-ignores —
so a path outside that root falls back to matching the BASENAME only. Two of
ruff.toml's three carve-outs therefore worked under the gate (``**/test_*.py``,
``**/*_test.py``) and one could never match at all (``**/tests/**``).

This one was not latent here. ``tests/endpoint_test_utils.py`` is a real helper
under ``tests/`` named neither way, and it asserts. Measured against the pre-fix
ratchet: adding a single assert to it took the S101 count 9 -> 10 and BLOCKED
the commit, while ``ruff check`` on the real tree exempts the file outright.
Post-fix both counts are 0. Two tools disagreeing about what a test is, which is
the mismatch the shared ``is_test_file`` was extracted to prevent (Factory#403).

The fix is not a better temp path: mirroring the directories inside the temp dir
(``<tmpdir>/tests/helpers.py``) still reports S101, measured. Ruff is told the
file's REAL repo-relative path via ``--stdin-filename`` and gets the source on
stdin, so there is no copy to misjudge.

The second test is the one with teeth: exempting unconditionally — or losing the
path in a way that made everything look like a test — passes the first and
silently drops S101 for the whole repo.
"""

from __future__ import annotations

import ratchet_helpers as rh

# scripts/ is put on sys.path by tests/conftest.py.
import ratchet_lint as rl

_ASSERTION = "assert x is None\n"


def test_ruff_exempts_every_shape_of_test_path() -> None:
    named = rl.ruff_counts(_ASSERTION, "tests/test_x.py")
    helper = rl.ruff_counts(_ASSERTION, "tests/endpoint_test_utils.py")
    assert "S101" not in helper, "a helper under tests/ must get the test assert carve-out"
    assert helper == named, "two files under tests/ must not get two different verdicts"


def test_ruff_still_holds_production_to_the_assert_bar() -> None:
    # THE ASSERTION WITH TEETH.
    assert "S101" in rl.ruff_counts(_ASSERTION, "apps/backend/pfactory/prod.py")


def test_excludes_are_still_applied_by_the_ratchet_not_by_ruff() -> None:
    """``--stdin-filename`` fixes per-file-IGNORES, not ``extend-exclude``.

    Measured: ruff lints whatever it is handed explicitly, stdin included, so a
    path in ruff.toml's extend-exclude still comes back with violations from
    ruff_counts. The exclusion has to stay in `_is_excluded`, applied before a
    file ever reaches ruff — deleting it as "now redundant" would silently start
    gating the vendored mirrors this repo keeps byte-exact.
    """
    excluded = "scripts/ratchet_helpers.py"
    assert rl._is_excluded(excluded, rl._ruff_excludes()), "fixture must name a truly excluded path"
    assert rl.ruff_counts(_ASSERTION, excluded), "ruff does not self-exclude explicit input"


def test_the_ruff_rule_lives_in_the_canonical_module() -> None:
    """The ratchet must CONSUME the shared rules, not carry its own copy."""
    for name in ("is_test_file", "ruff_stdin_argv", "MYPY_TEST_RELAX"):
        assert getattr(rl, name) is getattr(rh, name), f"{name} is not the canonical object"
