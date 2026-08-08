#!/usr/bin/env python3
"""A linter that never ran must fail the ratchet, not read as "no violations".

PFactory#455 (ruff), same shape found and fixed in TFactory#951.

``ruff check`` exits 0 clean, 1 with violations, and >=2 on its OWN failure -
binary missing, config parse error, bad argv - writing nothing to stdout. A
CLEAN run prints ``[]``, never nothing. So empty stdout was never the clean
case, and treating it as one let the base-vs-head comparison come back 0 == 0
and report "no regression" having measured nothing.

``mypy`` has the same three-way exit code, with one wrinkle: it also exits 2 on
a BLOCKING error (a syntax error in the file under test). That case still names
the file, so it is counted and gated normally; only a failed run that attributed
nothing is treated as "did not run".

The controls in both halves are the ones with teeth. A guard that fired on every
non-zero exit would break the ordinary "violations found" path (exit 1), and a
guard keyed only on the exit code would abort the ratchet on a file with a
syntax error rather than counting it.
"""

from __future__ import annotations

import sys

import pytest

# scripts/ is put on sys.path by tests/conftest.py.
import ratchet_lint as rl


class _Res:
    """The subset of CompletedProcess the ratchet reads."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub_ruff(monkeypatch: pytest.MonkeyPatch, res: _Res) -> None:
    monkeypatch.setattr(rl, "_run", lambda *_a, **_k: res)


def _stub_mypy(monkeypatch: pytest.MonkeyPatch, res: _Res) -> None:
    # mypy_errors() shells out through subprocess.run directly, not _run.
    monkeypatch.setattr(rl.subprocess, "run", lambda *_a, **_k: res)


def _mypy_errors() -> int:
    return rl.mypy_errors("apps/backend/pfactory/prod.py", "apps/backend", "mypy.ini")


# mypy is invoked from INSIDE the package (issue #466), so the paths it prints
# are relative to that dir, not to the repo root. The stubs below must emit what
# real mypy emits or they would assert against a shape the ratchet never sees.
_PROD = "pfactory/prod.py"
_OTHER = "pfactory/other.py"


# --------------------------------------------------------------------------- #
# ruff                                                                         #
# --------------------------------------------------------------------------- #


def test_ruff_own_failure_exits_rather_than_reporting_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ruff(monkeypatch, _Res(2, stderr="error: invalid value for '--config'"))
    with pytest.raises(SystemExit) as exc:
        rl.ruff_counts("x = 1\n", "apps/backend/pfactory/prod.py")
    assert exc.value.code == 2


def test_ruff_failure_surfaces_stderr_for_diagnosis(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_ruff(monkeypatch, _Res(2, stderr="ruff.toml does not point to a config"))
    with pytest.raises(SystemExit):
        rl.ruff_counts("x = 1\n", "apps/backend/pfactory/prod.py")
    assert "ruff.toml does not point to a config" in capsys.readouterr().err


def test_ruff_clean_file_still_counts_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # Control: exit 0 with "[]" is ruff saying "checked it, nothing wrong".
    _stub_ruff(monkeypatch, _Res(0, stdout="[]"))
    assert rl.ruff_counts("x = 1\n", "apps/backend/pfactory/prod.py") == {}


def test_ruff_violations_are_still_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    # Control: exit 1 is the ordinary "found something" path, not a failure.
    _stub_ruff(monkeypatch, _Res(1, stdout='[{"code": "S101"}, {"code": "S101"}]'))
    assert rl.ruff_counts("x = 1\n", "apps/backend/pfactory/prod.py")["S101"] == 2


def test_ruff_writing_nothing_at_all_exits_rather_than_counting_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory#648: empty stdout was never the clean case.

    A clean run prints `[]`. Empty stdout on an exit-0 run is ruff having
    written no report, and the `return Counter()` that used to sit here counted
    it as perfection -- the same nothing-reads-as-clean defect Factory#590
    closed one exit code over, which `require_tool_ran` cannot reach because the
    process exited 0.

    This is the WIRING proof: that this fork routes its parse through
    `ratchet_helpers.ruff_findings` rather than restating it. No byte comparison
    can see a restatement, which is why the rule is also registered in the hub
    gate's _REQUIRED_RATCHET_RULES.
    """
    _stub_ruff(monkeypatch, _Res(0, stdout="   \n"))
    with pytest.raises(SystemExit) as exc:
        rl.ruff_counts("x = 1\n", "apps/backend/pfactory/prod.py")
    assert exc.value.code == 2


def test_ruff_output_that_is_not_json_exits_rather_than_counting_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Factory#648: with `fix = true` reachable in a config ruff writes the FIXED
    # SOURCE to stdout and exits 0, so the parse would read Python as findings.
    # The canonical now says so; this used to be a bare `except` with no message.
    _stub_ruff(monkeypatch, _Res(0, stdout="import os\n\nx = 1\n"))
    with pytest.raises(SystemExit) as exc:
        rl.ruff_counts("x = 1\n", "apps/backend/pfactory/prod.py")
    assert exc.value.code == 2
    assert "not the JSON finding list" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# mypy                                                                         #
# --------------------------------------------------------------------------- #


def test_mypy_own_failure_exits_rather_than_reporting_zero_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_mypy(monkeypatch, _Res(2, stderr="mypy: error: Cannot find config file"))
    with pytest.raises(SystemExit) as exc:
        _mypy_errors()
    assert exc.value.code == 2


def test_mypy_failure_surfaces_stderr_for_diagnosis(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_mypy(monkeypatch, _Res(2, stderr="mypy: error: unrecognized arguments"))
    with pytest.raises(SystemExit):
        _mypy_errors()
    assert "unrecognized arguments" in capsys.readouterr().err


def test_mypy_clean_file_still_counts_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # Control: exit 0 and no error lines is a genuinely clean file.
    _stub_mypy(monkeypatch, _Res(0, stdout="Success: no issues found in 1 source file\n"))
    assert _mypy_errors() == 0


def test_mypy_blocking_error_is_counted_not_treated_as_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Control with teeth: mypy exits 2 on a syntax error too, but it NAMES the
    # file. Keying the guard on the exit code alone would abort the ratchet here
    # instead of counting the error and gating on it.
    _stub_mypy(
        monkeypatch,
        _Res(2, stdout=f"{_PROD}:1: error: Invalid syntax  [syntax]\n"),
    )
    assert _mypy_errors() == 1


def test_mypy_errors_are_still_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    # Control: exit 1 is the ordinary "found something" path.
    out = (
        f"{_PROD}:3: error: Missing type annotation  [no-untyped-def]\n"
        f"{_PROD}:9: error: Returning Any  [no-any-return]\n"
    )
    _stub_mypy(monkeypatch, _Res(1, stdout=out))
    assert _mypy_errors() == 2


def test_mypy_ignores_errors_belonging_to_other_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Control: an error mypy surfaces in an imported module is that file's, and
    # with exit 1 the run succeeded, so zero here is honest.
    _stub_mypy(monkeypatch, _Res(1, stdout=f"{_OTHER}:3: error: nope\n"))
    assert _mypy_errors() == 0


# --------------------------------------------------------------------------- #
# mypy invocation: one module name per file (issue #466)                       #
# --------------------------------------------------------------------------- #


def test_mypy_runs_from_inside_the_package_with_one_module_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The invocation that stops mypy resolving a file under two module names.

    Issue #466: run from the repo root, ``apps/backend/plan/...`` resolved as
    both ``plan.*`` (via MYPYPATH) and ``backend.*`` (by crawling up the stray
    ``apps/backend/__init__.py``), so mypy exited 2 without checking anything
    and the ratchet's mypy half measured nothing for seven weeks.

    Three things have to hold together, and each is asserted because dropping
    any one of them brings the ambiguity back:

    * ``cwd`` is the package dir, and the file is named RELATIVE to it -- a
      repo-root path re-introduces the root as a second base;
    * ``--explicit-package-bases`` so the module name comes from MYPYPATH
      rather than from crawling up ``__init__.py`` files, which re-derives the
      second name even from inside the package;
    * ``--namespace-packages``, which ``--explicit-package-bases`` requires.
    """
    seen: dict[str, object] = {}

    def _record(argv: list[str], **kwargs: object) -> _Res:
        seen["argv"] = argv
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env")
        return _Res(0, stdout="Success: no issues found in 1 source file\n")

    monkeypatch.setattr(rl.subprocess, "run", _record)
    assert _mypy_errors() == 0

    argv = seen["argv"]
    assert isinstance(argv, list)
    assert "--explicit-package-bases" in argv
    assert "--namespace-packages" in argv
    # The file is named relative to the package, never by its repo-root path.
    assert argv[-1] == _PROD
    assert "apps/backend" not in argv[-1]
    # ...and mypy is actually run from in there.
    assert str(seen["cwd"]).endswith("apps/backend")
    # The package itself is the first import base, mirroring runtime sys.path.
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["MYPYPATH"].split(":")[0] == "."


# --------------------------------------------------------------------------- #
# mypy target version: the venv being checked, not the fleet floor (issue #467) #
# --------------------------------------------------------------------------- #


def test_mypy_targets_the_interpreter_it_runs_under_not_the_baseline_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate must declare the Python it is actually checking against.

    Issue #467: ``standards/mypy.ini`` pins ``python_version = 3.11`` -- the
    fleet FLOOR, right for a baseline every repo inherits. This repo's venv is
    3.12, and numpy's stubs in it use PEP 695 ``type`` statements. Told to target
    3.11, mypy refuses to parse them and exits 2 having checked nothing, so 36
    files were hard-failed by ``require_tool_ran`` and 5 more were silently
    under-counted.

    Asserted against ``sys.version_info`` rather than a literal ``3.12``: a
    literal is precisely how 3.11 went stale, and a test written that way would
    go stale with it instead of catching the next bump.
    """
    seen: dict[str, object] = {}

    def _record(argv: list[str], **_kwargs: object) -> _Res:
        seen["argv"] = argv
        return _Res(0, stdout="Success: no issues found in 1 source file\n")

    monkeypatch.setattr(rl.subprocess, "run", _record)
    assert _mypy_errors() == 0

    argv = seen["argv"]
    assert isinstance(argv, list)
    expected = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert argv[argv.index("--python-version") + 1] == expected
    # The CLI flag has to WIN over the config file, so both must be present:
    # dropping --config-file loses the strict bar, dropping the version override
    # puts the 3.11 floor back and the numpy stubs stop parsing.
    assert "--config-file" in argv
