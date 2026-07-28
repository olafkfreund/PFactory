#!/usr/bin/env python3
"""Canonical helpers shared by every service's diff-scoped lint ratchet.

WHY THIS FILE EXISTS (Factory#403). The ratchet itself has five forks — hub
226L, PFactory 329L, TFactory 327L, CFactory 275L, AIFactory 302L — with only
three function names common to all of them. They are structurally different
programs, not stylistic variants, and consolidating them wholesale would mean
rewriting four blocking CI gates at once.

But the parts that MUST agree are small, and both of the ratchet bugs found on
2026-07-28 lived in exactly these two rules:

* how a temp copy is named — a randomised name defeated ruff per-file-ignores,
  so every net-new test file was held to the production bar (fixed via
  ``write_temp``)
* what counts as a test file — mypy applied the production type bar to tests,
  and the shared ``standards/mypy.ini`` cannot express the carve-out because
  per-module wildcards do not match top-level test modules (fixed via
  ``is_test_file``)

So this module is the canonical for the RULES, while each service keeps its own
orchestration. Same shape as ``shared/factory-github/``: a canonical layer, not
a canonical program.

Pure stdlib and side-effect free, so a service can vendor it byte-exact next to
its ratchet script and import it directly. It is byte-exact drift-gated by
``scripts/check_verification_core_drift.py``; edit the hub copy and re-vendor,
never a service copy.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

# Flags that relax the strict type bar for test files. mypy per-module sections
# cannot express this: a bare `[mypy-test_*]` (and even `[mypy-*]`) silently
# fails to match a top-level test module — measured, the error count was
# unchanged — and an exact-name section is not portable across four service
# layouts. The ratchet knows the path, so the decision belongs here.
MYPY_TEST_RELAX: tuple[str, ...] = (
    "--allow-untyped-defs",
    "--allow-incomplete-defs",
    # A test file imports pytest and its own app modules, which the ratchet
    # cannot resolve: it puts only the OWNING PACKAGE on MYPYPATH, so a
    # web-server test importing `server.auth` gets import-not-found. That is a
    # property of the harness, not a defect in the test — and since a NEW test
    # file has a base count of 0, it blocks the ratchet outright. The
    # alternative is a `type: ignore[import-not-found]` on every new test,
    # which is the suppress-the-guard failure mode this whole rule exists to
    # stop. Production files keep strict import checking.
    "--ignore-missing-imports",
)


def is_test_file(path: str) -> bool:
    """Does *path* name a test file?

    Deliberately in step with the ruff config's per-file-ignores
    (``**/test_*.py``, ``**/*_test.py``, ``**/tests/**``) so one tool cannot
    treat a file as a test while another holds it to the production bar. That
    mismatch is the bug this rule exists to prevent, so the two must move
    together.
    """
    norm = path.replace("\\", "/")
    name = norm.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{norm}"
        or "/test/" in f"/{norm}"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def write_temp(source: str, filename: str) -> tuple[str, str]:
    """Write *source* under the REAL basename inside a fresh temp dir.

    A random-prefixed name (the old ``NamedTemporaryFile`` suffix trick) defeats
    per-file-ignores like ``**/test_*.py``, so test files were held to the
    non-test bar and tripped S101 while being clean under their real path.

    Returns ``(tmpdir, tmp)`` for the caller to clean up. Note the residual
    limit: the file still lands in a temp DIRECTORY, so only BASENAME globs
    match — a path-based ignore such as ``**/tests/**`` still will not.
    """
    tmpdir = tempfile.mkdtemp()
    tmp = str(Path(tmpdir) / Path(filename).name)
    Path(tmp).write_text(source)
    return tmpdir, tmp
