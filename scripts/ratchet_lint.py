#!/usr/bin/env python3
"""Diff-scoped lint ratchet for the PFactory Python backend.

Implements the Factory coding-standards ratchet (coding-standards.md sections 0
and 4.6): the strict bar (`ruff` with the shared select set + `mypy --strict`)
is enforced on the files a PR changes, and a changed file MAY NOT REGRESS - i.e.
it may not gain ruff OR mypy violations relative to the PR base. Untouched legacy
hotspots are allowed until touched, and the existing legacy backlog inside a
touched file does not block (a whole-repo strict gate would be instantly red:
hundreds of legacy violations at adoption). New code and any net-new violation a
PR introduces are blocked.

Mechanism (ruff): for each changed Python file, count ruff violations (shared
config) at the PR base and at HEAD; fail if HEAD has more. `ruff format`
reflowing legacy lines never increases the count, so a pure-cleanup PR stays
green while genuine new violations are caught.

Mechanism (mypy): same no-regression model. For each changed Python file, run
`mypy --strict` (standards/mypy.ini) and count the errors mypy attributes to
that file, at the PR base and at HEAD; fail if HEAD has more. mypy is invoked
from inside the owning package with the file path relative to it and
`--explicit-package-bases --namespace-packages`, so each module has exactly one
name (issue #466: run from the repo root, `apps/backend/plan/...` resolved as
both `plan.*` and `backend.*` via the stray `apps/backend/__init__.py`, and mypy
exited 2 without checking anything). The target Python version comes from the
interpreter the gate runs under, not from the shared baseline's floor of 3.11
(issue #467 - see ``interpreter_target``). mypy needs the file at its real path for
import resolution, so the base count is taken by swapping the file's content to
its base version in place (HEAD content is restored afterwards, always). Errors
mypy reports in OTHER files (imported modules) are not attributed to the changed
file and so never gate it.

Originally vendored from CFactory/scripts/ratchet_lint.py (intentional
cross-service reuse of the Factory shared ratchet); PACKAGE_DEFAULT matches this
repo's backend layout, and the blocking per-file mypy gate (issue #192) was
added here.

Pre-commit mode (issue #389): ``--staged`` gates the git INDEX against HEAD
with the exact same per-file no-regression rule and the exact same config, so
the .husky/pre-commit hook and CI cannot disagree. Staged content is read from
the index (``git show :<path>``), not the worktree, so the gate judges what
would actually be committed. Staged mode is ruff-only (implies ``--no-mypy``):
the mypy gate needs files on disk at their real paths and is too slow for a
hook; it stays in CI.

Usage:
    python scripts/ratchet_lint.py --base <git-ref> [--package <dir>] \\
        [--mypy-config <ini>] [--no-mypy]
    python scripts/ratchet_lint.py --staged [--package <dir>]

Exit code 0 if no changed file regressed; 1 otherwise.
"""

# This is a CLI ratchet tool: `print` IS its reporting surface (it writes the
# gate verdict to stdout for CI logs), so T20 (no-print-in-service-code) does not
# apply file-wide. Coded directive (not a blanket noqa) to satisfy PGH004.
# ruff: noqa: T201

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import tomllib
from collections import Counter
from functools import cache
from pathlib import Path

# Canonical shared ratchet rules, vendored byte-exact from the Factory hub
# and byte-exact drift-gated (Factory#403). scripts/ is sys.path[0] when this
# runs as a script, so the sibling import resolves without packaging.
# write_temp is deliberately NOT imported: mypy runs on the file in place here
# (see mypy_errors) and ruff is fed stdin, so nothing in this fork needs a temp
# copy any more.
from ratchet_helpers import (
    MYPY_TEST_RELAX,
    is_test_file,
    require_tool_ran,
    ruff_findings,
    ruff_stdin_argv,
)

PACKAGE_DEFAULT = "apps/backend"
MYPY_CONFIG_DEFAULT = "standards/mypy.ini"
# mypy emits "<path>:<line>: error: <msg>  [code]"; count only real errors.
_MYPY_ERROR_RE = re.compile(r"^(?P<path>.+?):\d+: error:")


def _run(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    # Inputs are tool/git argv assembled from CI-controlled config, not untrusted
    # user data; this is a developer CI tool, not a network surface.
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, check=False, input=stdin
    )


def _ruff_excludes() -> list[str]:
    """Exclude globs from the repo ruff config (root ``ruff.toml`` + ``extend``).

    Ruff lints whatever it is handed explicitly and applies ``extend-exclude``
    only when it walks the tree itself — so the excludes never match here, and
    that is still true now the ratchet feeds it stdin under the real path
    (Factory#510 fixed the per-file-IGNORES, not this). Vendored mirrors
    (e.g. the factory-github layer, whose fidelity is enforced by its own
    drift gate, not the local linter) are excluded in ruff.toml; honour that
    here so the ratchet does not gate files ruff is configured to skip.
    """
    patterns: list[str] = []
    root = Path("ruff.toml")
    seen: set[str] = set()
    stack = [root]
    while stack:
        cfg = stack.pop()
        if not cfg.is_file() or str(cfg) in seen:
            continue
        seen.add(str(cfg))
        try:
            data = tomllib.loads(cfg.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        for key in ("exclude", "extend-exclude"):
            val = data.get(key)
            if isinstance(val, list):
                patterns.extend(str(x) for x in val)
        extend = data.get("extend")
        if isinstance(extend, str):
            stack.append(cfg.parent / extend)
    return patterns


def _is_excluded(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        # Match the full relative path or the basename, mirroring ruff's
        # behaviour for both directory and file globs.
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, f"*/{pat}"):
            return True
        if path == pat or path.endswith("/" + pat):
            return True
    return False


def owning_package(path: str, packages: list[str]) -> str:
    """Which of *packages* the file lives under.

    Needed because ``package`` is used as an IMPORT ROOT for mypy
    (MYPYPATH/PYTHONPATH), not merely as a filter. A file under
    ``apps/web-server`` type-checked with ``apps/backend`` on the path resolves
    its first-party imports against the wrong tree and reports errors that say
    more about the root than the code — so each file must be checked against the
    package it actually belongs to (Factory#384).

    The LONGEST match wins, so a nested package beats its parent.
    """
    target = Path(path)
    matches = [pkg for pkg in packages if Path(pkg) in target.parents or Path(pkg) == target.parent]
    return max(matches, key=len) if matches else packages[0]


def changed_python_files(base: str, packages: list[str], *, staged: bool = False) -> list[str]:
    """Python files under any of *packages* changed (added/modified) vs *base*.

    In staged mode the change set is the git index vs HEAD (what a commit in
    progress would actually record), not a committed range.
    """
    if staged:
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    else:
        cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"]
    res = _run(cmd)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        sys.exit(2)
    pkgs = [Path(p) for p in packages]
    excludes = _ruff_excludes()
    out: list[str] = []
    for line in res.stdout.splitlines():
        path = Path(line)
        if path.suffix != ".py" or not path.exists():
            continue
        # Skip files the repo ruff config excludes (e.g. vendored mirrors gated
        # by their own drift check, not the local linter).
        if _is_excluded(str(path), excludes):
            continue
        # Accept files inside the package dir, or the package dir itself if it is
        # a flat directory (e.g. scripts/).
        if any(pkg in path.parents or pkg == path.parent for pkg in pkgs):
            out.append(str(path))
    return out


def ruff_counts(source: str, filename: str) -> Counter[str]:
    """Per-rule ruff violation counts for *source* checked as *filename*.

    Fed on stdin under the file's REAL path so ruff's per-file-ignores see the
    same path ``ruff check`` would (Factory#510). The temp copy this used to
    write could not: ruff relativises a path against the project root before
    matching the globs, and a path OUTSIDE that root falls back to the BASENAME
    only. ``**/test_*.py`` and ``**/*_test.py`` therefore matched a copy but
    ``**/tests/**`` never could, so a helper under ``tests/`` named neither way
    was held to the production assert bar the real tree exempts it from.

    This does NOT extend to ``extend-exclude`` — measured. Ruff lints whatever
    it is handed explicitly, stdin included, so the exclude globs still have to
    be applied by :func:`_is_excluded` before a file gets here.
    """
    res = _run(ruff_stdin_argv("ruff.toml", filename), stdin=source)
    # The shared "is this run a measurement" rule, both halves (Factory#590 for
    # the exit code, Factory#648 for the output). This used to be an exit-code
    # check plus a `return Counter()` for empty stdout plus a bare
    # `except json.JSONDecodeError`, restated here and in the four sibling
    # ratchets. The empty-stdout branch was the one with teeth: the pinned ruff
    # prints `[]` for a clean run -- including for empty stdin -- so empty
    # stdout was always ruff writing no report, counted as zero violations.
    # Both verdicts now live in the drift-gated canonical.
    return ruff_findings(res)


@cache
def rename_sources(base: str, staged: bool = False) -> tuple[tuple[str, str], ...]:
    """``(head_path, base_path)`` pairs for files this diff MOVED.

    The other half of the ``ACMR`` change above (TFactory#1005). Once renames
    are visible, looking a moved file up on base by its HEAD path finds nothing
    and reads the baseline as **0**, so every pre-existing violation in it
    reports as net-new. AIFactory's fork had exactly that and made a pure
    ``git mv`` of a legacy file report ``0 -> 167`` — a gate punishing the
    cleanup it exists to encourage (AIFactory#1218).

    ``staged`` MUST match the scope ``changed_python_files`` used. This fork is
    the only one with a staged mode, and a committed-range lookup there would
    leave the pre-commit lane blind to exactly the renames CI now sees — the
    half-fix that is worse than none, because the two lanes would disagree.

    Cached per (base, staged) — one subprocess, not one per file. Returns a
    tuple rather than a dict because the value is CACHED and therefore shared:
    a mutable one could be modified by one caller and observed by the next.
    """
    scope = ["--cached"] if staged else [f"{base}...HEAD"]
    res = _run(["git", "diff", *scope, "--name-status", "-M", "--diff-filter=R"])
    if res.returncode != 0:
        # No rename information available: fall back to identity mapping rather
        # than failing. Worst case is the pre-fix behaviour for moved files.
        return ()
    pairs: list[tuple[str, str]] = []
    for line in res.stdout.splitlines():
        # `R<similarity>\told\tnew`
        status, _, paths = line.partition("\t")
        old, _, new = paths.partition("\t")
        if status.startswith("R") and old and new:
            pairs.append((new, old))
    return tuple(pairs)


def file_at_base(base: str, path: str, *, staged: bool = False) -> str | None:
    """The file's content on *base*, following a rename to its old path.

    Identity (the ``path`` the counter judges by) deliberately stays the HEAD
    path in the callers — only the CONTENT comes from the old location. Judging
    the two sides under different per-file-ignores is Factory#510.
    """
    src = dict(rename_sources(base, staged)).get(path, path)
    res = _run(["git", "show", f"{base}:{src}"])
    return res.stdout if res.returncode == 0 else None


def staged_source(path: str) -> str | None:
    """The INDEX content of *path* (what `git commit` would record), or None."""
    res = _run(["git", "show", f":{path}"])
    return res.stdout if res.returncode == 0 else None


def regressions(base: str, path: str, *, staged: bool = False) -> list[str]:
    head_src = staged_source(path) if staged else Path(path).read_text()
    if head_src is None:
        return []
    head_counts = ruff_counts(head_src, path)
    base_src = file_at_base(base, path, staged=staged)
    base_counts = ruff_counts(base_src, path) if base_src is not None else Counter()
    out: list[str] = []
    for code, head_n in head_counts.items():
        base_n = base_counts.get(code, 0)
        if head_n > base_n:
            out.append(f"{path}: {code} +{head_n - base_n} (base {base_n} -> head {head_n})")
    return out


def interpreter_target() -> str:
    """The ``--python-version`` this gate must target: the venv it checks against.

    The shared ``standards/mypy.ini`` declares ``python_version = 3.11``. That is
    correct for the hub baseline -- it is the fleet FLOOR (coding-standards.md
    section 1, "Python (3.11+)"), the hub's own ratchet still builds 3.11, and
    raising it centrally would raise the floor for every repo. It is wrong as
    THIS gate's target: the venv whose site-packages mypy reads is 3.12, and
    numpy's stubs there use PEP 695 ``type`` statements. Told to target 3.11 mypy
    refuses to parse them, exits 2 having checked nothing, and every file that
    reaches numpy transitively is ungated (issue #467: 36 files hard-failed by
    require_tool_ran, plus 5 more that reported one unrelated import error and so
    passed the guard while their real counts, 4 to 28, went unmeasured).

    Derived from the running interpreter rather than written as ``3.12``, because
    a literal is exactly how ``3.11`` went stale: the venv moves and the target
    does not. ``mypy`` comes from that same venv (the workflow puts it first on
    PATH and runs this script with its python), so its version is this process's.

    Not a loosening under the tighten-only rule: every strict flag in the shared
    baseline still applies, unchanged. Only the syntax/stdlib level moves, and it
    moves to the one actually in use -- which is what mypy would default to on
    its own were the baseline not naming a version.
    """
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def mypy_errors(path: str, package: str, mypy_config: str) -> int:
    """Number of mypy --strict errors attributed to *path*.

    Runs mypy FROM INSIDE *package*, with the file named relative to it, so the
    package is the one and only import root — mirroring the app's runtime
    ``sys.path`` (issue #466). Invoked from the repo root instead, mypy reaches
    the same file two ways: as ``plan.service`` via ``MYPYPATH=apps/backend``,
    and as ``backend.plan.service`` by walking up the stray
    ``apps/backend/__init__.py`` from the root. It then aborts the whole run
    with "Source file found twice under different module names" and exits 2
    having checked nothing. That is not a mypy quirk to route around: two names
    for one module is genuinely ambiguous, and the fix is to leave exactly one.

    ``--explicit-package-bases --namespace-packages`` is what makes the cwd
    decisive: without them mypy still crawls up through ``__init__.py`` files and
    re-derives the second name from inside the package too. With them the module
    name comes from the MYPYPATH bases below, nothing else. Same treatment
    TFactory's ratchet already applies for the same stray ``__init__.py``.

    Only error lines whose location is the file itself are counted (errors
    surfaced in imported modules belong to those files, not the changed one).
    """
    pkg = Path(package).resolve()
    rel = os.path.relpath(Path(path).resolve(), pkg)
    relax = MYPY_TEST_RELAX if is_test_file(path) else []
    env = dict(os.environ)
    # The package dir is the import base, mirroring the app's runtime sys.path.
    # Sibling app packages are appended because the web server imports the
    # backend at runtime; without them mypy cannot resolve `plan.*` from a
    # web-server file and reports import-not-found, which is unfixable from the
    # file itself and would block any NEW file, whose base count is 0. They are
    # separate trees, so they add no second name for anything under `.`.
    siblings = [
        os.path.relpath(p, pkg)
        for p in sorted(pkg.parent.iterdir())
        if p.is_dir() and p != pkg and not p.name.startswith(".")
    ]
    search = os.pathsep.join([".", *siblings])
    for var in ("MYPYPATH", "PYTHONPATH"):
        env[var] = search
    # The config path is repo-root-relative; the child runs in the package dir.
    config = os.path.relpath(Path(mypy_config).resolve(), pkg)
    # CI-controlled argv (see _run); mypy is resolved from PATH (the pinned venv
    # is put first on PATH by the workflow), matching how the ruff ratchet shells
    # out to `ruff`.
    res = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "mypy",
            "--config-file",
            config,
            "--python-version",
            interpreter_target(),
            "--explicit-package-bases",
            "--namespace-packages",
            *relax,
            rel,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(pkg),
        env=env,
    )
    target = Path(rel)
    count = 0
    for line in res.stdout.splitlines():
        match = _MYPY_ERROR_RE.match(line)
        if match is not None and Path(match.group("path")) == target:
            count += 1
    # Same shared rule as the ruff counter, with `measured` passed: mypy's exit 2
    # also covers a BLOCKING error, which still names a file and so belongs in the
    # count rather than aborting the run.
    require_tool_ran("mypy", res, measured=count)
    return count


def mypy_regression(base: str, path: str, package: str, mypy_config: str) -> str | None:
    """A no-regression message if *path* gains mypy errors vs *base*, else None.

    The base count needs the file's base content at its real path (so imports
    still resolve); the HEAD content is restored unconditionally afterwards.
    """
    head_n = mypy_errors(path, package, mypy_config)
    base_src = file_at_base(base, path)
    if base_src is None:
        # New file: every error is net-new; base count is zero.
        base_n = 0
    else:
        target = Path(path)
        head_src = target.read_text()
        try:
            target.write_text(base_src)
            base_n = mypy_errors(path, package, mypy_config)
        finally:
            target.write_text(head_src)
    if head_n > base_n:
        return f"{path}: mypy +{head_n - base_n} errors (base {base_n} -> head {head_n})"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="git ref to diff against (required unless --staged)")
    parser.add_argument(
        "--staged",
        action="store_true",
        help=(
            "pre-commit mode: gate the git index against HEAD instead of a "
            "committed range; ruff-only (implies --no-mypy, which stays in CI)"
        ),
    )
    # Repeatable (Factory#384): apps/web-server holds every FastAPI route and
    # request model and was gated by nothing, which is how 133 credential-bearing
    # pydantic fields (#377) accumulated there unnoticed. Default unchanged, so
    # an existing `--package apps/backend` invocation behaves exactly as before.
    parser.add_argument("--package", action="append", dest="packages", default=None)
    parser.add_argument(
        "--mypy-config",
        default=MYPY_CONFIG_DEFAULT,
        help="mypy config file for the strict per-file gate",
    )
    parser.add_argument(
        "--no-mypy",
        action="store_true",
        help="skip the mypy no-regression gate (ruff-only)",
    )
    args = parser.parse_args()

    if args.staged and args.base:
        parser.error("--base and --staged are mutually exclusive")
    if not args.staged and not args.base:
        parser.error("--base is required unless --staged")
    base = "HEAD" if args.staged else args.base
    no_mypy = args.no_mypy or args.staged

    packages = args.packages or [PACKAGE_DEFAULT]
    files = changed_python_files(base, packages, staged=args.staged)
    if not files:
        print(f"ratchet: no changed Python files under {packages}; nothing to gate.")
        return 0

    print("ratchet: gating changed files:\n  " + "\n  ".join(files))

    ruff_regressions: list[str] = []
    for path in files:
        ruff_regressions.extend(regressions(base, path, staged=args.staged))

    mypy_regressions: list[str] = []
    if not no_mypy:
        for path in files:
            msg = mypy_regression(base, path, owning_package(path, packages), args.mypy_config)
            if msg is not None:
                mypy_regressions.append(msg)

    failed = False
    if ruff_regressions:
        failed = True
        print("\nratchet FAILED: changed files gained ruff violations (shared strict bar):")
        for line in ruff_regressions:
            print(f"  {line}")

    if mypy_regressions:
        failed = True
        print("\nratchet FAILED: changed files gained mypy --strict errors:")
        for line in mypy_regressions:
            print(f"  {line}")

    if failed:
        print(
            "\nFix the new violations (or clean the file further). The ratchet only "
            "blocks NET-NEW violations - pre-existing legacy in a touched file is "
            "allowed (coding-standards.md section 4.6)."
        )
        return 1

    suffix = "" if no_mypy else " (ruff + mypy)"
    print(f"ratchet PASSED: no changed file regressed{suffix}; new violations: none.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
