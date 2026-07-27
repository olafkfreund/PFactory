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
that file, at the PR base and at HEAD; fail if HEAD has more. mypy needs the
file at its real path for import resolution, so the base count is taken by
swapping the file's content to its base version in place (HEAD content is
restored afterwards, always). Errors mypy reports in OTHER files (imported
modules) are not attributed to the changed file and so never gate it.

Originally vendored from CFactory/scripts/ratchet_lint.py (intentional
cross-service reuse of the Factory shared ratchet); PACKAGE_DEFAULT matches this
repo's backend layout, and the blocking per-file mypy gate (issue #192) was
added here.

Usage:
    python scripts/ratchet_lint.py --base <git-ref> [--package <dir>] \\
        [--mypy-config <ini>] [--no-mypy]

Exit code 0 if no changed file regressed; 1 otherwise.
"""

# This is a CLI ratchet tool: `print` IS its reporting surface (it writes the
# gate verdict to stdout for CI logs), so T20 (no-print-in-service-code) does not
# apply file-wide. Coded directive (not a blanket noqa) to satisfy PGH004.
# ruff: noqa: T201

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from pathlib import Path

PACKAGE_DEFAULT = "apps/backend"
MYPY_CONFIG_DEFAULT = "standards/mypy.ini"
# mypy emits "<path>:<line>: error: <msg>  [code]"; count only real errors.
_MYPY_ERROR_RE = re.compile(r"^(?P<path>.+?):\d+: error:")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # Inputs are tool/git argv assembled from CI-controlled config, not untrusted
    # user data; this is a developer CI tool, not a network surface.
    return subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603


def _ruff_excludes() -> list[str]:
    """Exclude globs from the repo ruff config (root ``ruff.toml`` + ``extend``).

    The ratchet writes each changed file to a temp path before checking, so
    ruff's own path-based ``extend-exclude`` never matches. Vendored mirrors
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
    matches = [
        pkg for pkg in packages
        if Path(pkg) in target.parents or Path(pkg) == target.parent
    ]
    return max(matches, key=len) if matches else packages[0]


def changed_python_files(base: str, packages: list[str]) -> list[str]:
    """Python files under any of *packages* changed (added/modified) vs *base*."""
    res = _run(["git", "diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD"])
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
    """Per-rule ruff violation counts for *source* checked as *filename*."""
    suffix = Path(filename).name
    with tempfile.NamedTemporaryFile("w", suffix=f"__{suffix}", delete=False) as fh:
        fh.write(source)
        tmp = fh.name
    try:
        res = _run(["ruff", "check", "--config", "ruff.toml", "--output-format", "json", tmp])
        if not res.stdout.strip():
            return Counter()
        try:
            items = json.loads(res.stdout)
        except json.JSONDecodeError:
            sys.stderr.write(res.stdout + res.stderr)
            sys.exit(2)
        return Counter(item["code"] for item in items)
    finally:
        Path(tmp).unlink(missing_ok=True)


def file_at_base(base: str, path: str) -> str | None:
    res = _run(["git", "show", f"{base}:{path}"])
    return res.stdout if res.returncode == 0 else None


def regressions(base: str, path: str) -> list[str]:
    head_src = Path(path).read_text()
    head_counts = ruff_counts(head_src, path)
    base_src = file_at_base(base, path)
    base_counts = ruff_counts(base_src, path) if base_src is not None else Counter()
    out: list[str] = []
    for code, head_n in head_counts.items():
        base_n = base_counts.get(code, 0)
        if head_n > base_n:
            out.append(f"{path}: {code} +{head_n - base_n} (base {base_n} -> head {head_n})")
    return out


def mypy_errors(path: str, package: str, mypy_config: str) -> int:
    """Number of mypy --strict errors attributed to *path*.

    Runs mypy on the file in place so imports resolve against the package, then
    counts only error lines whose location is *path* itself (errors surfaced in
    imported modules belong to those files, not the changed one).
    """
    env = dict(os.environ)
    # Make the package importable so mypy can follow first-party imports.
    for var in ("MYPYPATH", "PYTHONPATH"):
        existing = env.get(var)
        env[var] = f"{package}{os.pathsep}{existing}" if existing else package
    # CI-controlled argv (see _run); mypy is resolved from PATH (the pinned venv
    # is put first on PATH by the workflow), matching how the ruff ratchet shells
    # out to `ruff`.
    res = subprocess.run(  # noqa: S603
        ["mypy", "--config-file", mypy_config, path],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    target = Path(path)
    count = 0
    for line in res.stdout.splitlines():
        match = _MYPY_ERROR_RE.match(line)
        if match is not None and Path(match.group("path")) == target:
            count += 1
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
    parser.add_argument("--base", required=True, help="git ref to diff against")
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

    packages = args.packages or [PACKAGE_DEFAULT]
    files = changed_python_files(args.base, packages)
    if not files:
        print(f"ratchet: no changed Python files under {packages}; nothing to gate.")
        return 0

    print("ratchet: gating changed files:\n  " + "\n  ".join(files))

    ruff_regressions: list[str] = []
    for path in files:
        ruff_regressions.extend(regressions(args.base, path))

    mypy_regressions: list[str] = []
    if not args.no_mypy:
        for path in files:
            msg = mypy_regression(
                args.base, path, owning_package(path, packages), args.mypy_config
            )
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

    suffix = "" if args.no_mypy else " (ruff + mypy)"
    print(f"ratchet PASSED: no changed file regressed{suffix}; new violations: none.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
