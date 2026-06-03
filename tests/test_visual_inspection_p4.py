"""Tests for Visual Inspection P4 — the write_paths_to_branch git helper (#170 / #174).

The store routes are exercised by apps/web-server/tests (TestClient); this covers
the backend git helper's dry-run + traversal + create contract.
"""

from __future__ import annotations

from tools.git_writer import write_paths_to_branch


def test_dry_run_assembles_argv_no_subprocess() -> None:
    r = write_paths_to_branch(
        "/some/repo",
        ["automated-test/run1/report.md", "automated-test/run1/meta.json"],
        "pfactory/visual-run1",
        "visual inspection run1",
        dry_run=True,
    )
    assert r.dry_run is True and r.ok is True
    assert r.committed_paths == (
        "automated-test/run1/report.md",
        "automated-test/run1/meta.json",
    )
    argv = [" ".join(a) for a in r.argv_log]
    assert any("checkout -B pfactory/visual-run1" in a for a in argv)
    assert any("add --" in a for a in argv)
    assert any("commit --no-verify -m visual inspection run1" in a for a in argv)
    assert any("rev-parse HEAD" in a for a in argv)


def test_traversal_paths_rejected() -> None:
    r = write_paths_to_branch("/repo", ["../etc/passwd"], "b", "m", dry_run=True)
    assert r.ok is False


def test_empty_paths_rejected() -> None:
    r = write_paths_to_branch("/repo", [], "b", "m", dry_run=True)
    assert r.ok is False


def test_create_invokes_runner_in_order(tmp_path) -> None:
    (tmp_path / "automated-test").mkdir()
    calls: list = []

    class _Res:
        returncode = 0
        stdout = "deadbeef"
        stderr = ""

    def runner(argv, *, cwd, stdin=None):
        calls.append(argv)
        return _Res()

    r = write_paths_to_branch(
        tmp_path, ["automated-test/run1/report.md"], "pfactory/visual-run1",
        "commit msg", dry_run=False, runner_fn=runner,
    )
    assert r.ok is True and r.dry_run is False
    assert r.commit_sha == "deadbeef"
    # checkout -B, add, commit, rev-parse → 4 invocations in order
    assert len(calls) == 4
    assert calls[0][3:6] == ["checkout", "-B", "pfactory/visual-run1"]
    assert calls[1][3] == "add"
    assert calls[2][3] == "commit"
    assert calls[3][3] == "rev-parse"


def test_missing_repo_dir_errors_on_real_run() -> None:
    r = write_paths_to_branch("/definitely/not/a/repo", ["a/b.md"], "b", "m", dry_run=False)
    assert r.ok is False and "does not exist" in (r.error or "")
