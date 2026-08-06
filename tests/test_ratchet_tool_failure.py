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
        _Res(2, stdout="apps/backend/pfactory/prod.py:1: error: Invalid syntax  [syntax]\n"),
    )
    assert _mypy_errors() == 1


def test_mypy_errors_are_still_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    # Control: exit 1 is the ordinary "found something" path.
    out = (
        "apps/backend/pfactory/prod.py:3: error: Missing type annotation  [no-untyped-def]\n"
        "apps/backend/pfactory/prod.py:9: error: Returning Any  [no-any-return]\n"
    )
    _stub_mypy(monkeypatch, _Res(1, stdout=out))
    assert _mypy_errors() == 2


def test_mypy_ignores_errors_belonging_to_other_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Control: an error mypy surfaces in an imported module is that file's, and
    # with exit 1 the run succeeded, so zero here is honest.
    _stub_mypy(monkeypatch, _Res(1, stdout="apps/backend/pfactory/other.py:3: error: nope\n"))
    assert _mypy_errors() == 0
