#!/usr/bin/env python3
"""Canonical helpers shared by every service's diff-scoped lint ratchet.

WHY THIS FILE EXISTS (Factory#403). The ratchet itself has five forks — hub
226L, PFactory 329L, TFactory 327L, CFactory 275L, AIFactory 302L — with only
three function names common to all of them. They are structurally different
programs, not stylistic variants, and consolidating them wholesale would mean
rewriting four blocking CI gates at once.

But the parts that MUST agree are small, and both of the ratchet bugs found on
2026-07-28 lived in exactly these two rules:

* what path ruff judges a file by — a randomised temp name defeated ruff
  per-file-ignores, so every net-new test file was held to the production bar
  (``write_temp``), and even the real basename could not rescue the PATH globs
  because a temp copy lives outside the project root (fixed for good via
  ``ruff_stdin_argv``, Factory#510 — ruff is told the real path instead of
  being handed a copy)
* what counts as a test file — mypy applied the production type bar to tests,
  and the shared ``standards/mypy.ini`` cannot express the carve-out because
  per-module wildcards do not match top-level test modules (fixed via
  ``is_test_file``)

A third rule joined them on 2026-08-05 (Factory#590), and it is the one that
proves the shape: whether the linter ACTUALLY RAN. ``ruff_counts()`` read empty
ruff stdout as "no violations" when a clean run prints ``[]`` and empty stdout
means ruff failed, so a blocking gate reported green on a linter that never ran.
Found in PFactory#455, found independently a day earlier in TFactory#951, and
the sweep that followed found it in all five repos in BOTH the ruff and the mypy
half — nine guards, five PRs, five chances to get one subtly wrong. TFactory#951
itself shipped a half-fix, because ``mypy_errors()`` sat directly below the
function it corrected with the identical defect. That rule now lives here once,
as :func:`require_tool_ran`.

Note what this module deliberately does NOT hold: the invocation. The five
ratchets run mypy five genuinely different ways (in place with MYPYPATH, from
inside the package with ``--explicit-package-bases``, from a temp copy next to
the file, from a temp dir, from a git worktree) because their package layouts
differ. Those differences are real and load-bearing. The VERDICT on whether a
run produced a measurement is not, and that is the seam.

So this module is the canonical for the RULES, while each service keeps its own
orchestration. Same shape as ``shared/factory-github/``: a canonical layer, not
a canonical program.

Pure stdlib, so a service can vendor it byte-exact next to its ratchet script and
import it directly. It is byte-exact drift-gated by
``scripts/check_verification_core_drift.py``; edit the hub copy and re-vendor,
never a service copy.
"""

from __future__ import annotations

import subprocess
import sys
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


def require_tool_ran(
    tool: str,
    res: subprocess.CompletedProcess[str],
    *,
    measured: int = 0,
) -> None:
    """Abort the ratchet unless *tool* actually produced a measurement.

    ruff and mypy both exit 0 when clean and 1 when they found something. Any
    OTHER exit code is the tool failing for its own reasons — binary missing,
    config parse error, bad argv — and a failed run reports nothing. Nothing
    reads as zero, zero reads as clean, and BOTH sides of the base-vs-head
    comparison come back equal because the cause is environmental and hits both.
    The ratchet then passes green having measured nothing (Factory#590).

    *measured* is how many findings the caller already attributed to the file,
    and it is the entire difference between the two tools:

    * ruff writes nothing to stdout when it fails, so the caller has counted
      nothing yet and the default 0 is correct. Any exit >= 2 is fatal.
    * mypy exits 2 both for its own failure AND for a blocking error (a syntax
      error in the file under test). A blocking error still emits an error line,
      so it lands in the caller's count and must be gated as the regression it
      is. A ZERO count out of an exit-2 run is "did not run", not "clean".

    Raises ``SystemExit(2)`` — the ratchet's "could not measure" code, distinct
    from exit 1, which means "measured, and it regressed". Callers pass their own
    ``CompletedProcess``; this helper never invokes a subprocess itself, so the
    per-repo invocation strategy stays where it belongs.
    """
    if res.returncode in (0, 1) or measured:
        return
    sys.stderr.write(
        f"ratchet: {tool} exited {res.returncode} having reported nothing — it did not run. "
        "A gate cannot report clean on a tool that never measured anything.\n"
    )
    sys.stderr.write((res.stdout or "") + (res.stderr or ""))
    raise SystemExit(2)


def ruff_stdin_argv(config: str, filename: str) -> list[str]:
    """argv that counts ruff violations for source fed on STDIN as *filename*.

    Ruff decides per-file-ignores from the path it is told a file has, and
    ``--stdin-filename`` lets that be the REAL repo-relative path. No temp copy
    can (Factory#510): ruff relativises the path against the project root before
    matching the globs, and a path OUTSIDE that root falls back to matching the
    BASENAME only. So ``**/test_*.py`` and ``**/*_test.py`` matched a temp copy
    but ``**/tests/**`` could never — a test helper under ``tests/`` whose name
    is neither ``test_*.py`` nor ``*_test.py`` was held to the production assert
    bar by the ratchet while ``ruff check`` on the real tree exempted it. Two
    tools disagreeing about what a test is, which is what :func:`is_test_file`
    was extracted to prevent.

    Measured, because the obvious fix does not work: mirroring the directories
    inside the temp dir (``<tmpdir>/tests/helpers.py``) still reports S101. The
    path has to be inside the project root, and stdin is how you get that
    without writing into the tree being gated.

    The caller passes *source* on stdin (``subprocess.run(..., input=source)``).
    ``--output-format json`` so the caller counts by rule code.
    """
    return [
        "ruff",
        "check",
        "--config",
        config,
        "--stdin-filename",
        filename,
        "--output-format",
        "json",
        "-",
    ]


def write_temp(source: str, filename: str) -> tuple[str, str]:
    """Write *source* under the REAL basename inside a fresh temp dir.

    For MYPY only. Ruff no longer needs a temp copy at all — see
    :func:`ruff_stdin_argv`, which fixed the path-glob half of this problem that
    a temp copy structurally cannot. mypy has no ``--stdin-filename``, but it
    does not need one either: its test carve-out is decided by
    :func:`is_test_file` from the ORIGINAL path, not by mypy's own config
    matching the temp path.

    The real basename still matters here. A random-prefixed name (the old
    ``NamedTemporaryFile`` suffix trick) also defeats mypy's per-module sections
    and makes the error text unreadable.

    Returns ``(tmpdir, tmp)`` for the caller to clean up.
    """
    tmpdir = tempfile.mkdtemp()
    tmp = str(Path(tmpdir) / Path(filename).name)
    Path(tmp).write_text(source)
    return tmpdir, tmp
