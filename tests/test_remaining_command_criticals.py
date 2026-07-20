"""Guards for the CodeQL criticals that survived the launcher fix (#332).

Three sinks take user input into a command line or an outbound request:

* ``assert_safe_git_ref`` -- refs reaching ``git`` argv. No shell is involved,
  but git parses its own argv, and ``git log`` accepts ``--output=<file>``, so a
  ref permitted to begin with ``-`` is a file-write primitive.
* ``assert_safe_probe_url`` -- the MCP health check fetches a URL from the
  request body (``py/full-ssrf``).
* ``_safe_launch_path`` -- the worktree path still becomes an argument to the
  launched application even though the executable is now fixed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).resolve().parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytest.importorskip("fastapi")

from server.routes import tasks as tasks_mod  # noqa: E402
from server.routes.git import (  # noqa: E402
    UnsafeProbeURLError,
    assert_safe_probe_url,
)
from server.routes.tasks import _safe_launch_path  # noqa: E402
from server.services.git_utils import assert_safe_git_ref  # noqa: E402


@pytest.mark.parametrize(
    "ref",
    [
        "--all",
        "-n",
        "--output=/tmp/pwned",
        "--upload-pack=touch /tmp/x",
        "-",
        "",
        "v1..v2",  # would rewrite the range it is joined into
        "main; rm -rf /",
        "main branch",
    ],
)
def test_option_shaped_refs_are_rejected(ref: str) -> None:
    with pytest.raises(ValueError):
        assert_safe_git_ref(ref, "test")


@pytest.mark.parametrize(
    "ref", ["main", "HEAD", "v1.2.3", "refs/heads/feature", "feature/foo-1", "HEAD~3"]
)
def test_real_refs_still_pass(ref: str) -> None:
    assert assert_safe_git_ref(ref, "test") == ref


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",  # urllib will happily read local files
        "gopher://example.com/",
        "ftp://example.com/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[fe80::1]/",
        "notaurl",
        "http://",
    ],
)
def test_unsafe_probe_urls_are_refused(url: str) -> None:
    with pytest.raises(UnsafeProbeURLError):
        assert_safe_probe_url(url)


@pytest.mark.parametrize(
    "url", ["https://example.com/", "http://127.0.0.1:8080/", "http://10.0.0.5:9000/"]
)
def test_legitimate_probe_urls_are_allowed(url: str) -> None:
    """Private and loopback stay reachable: in-cluster MCP servers are the norm."""
    assert_safe_probe_url(url)


def test_loopback_is_allowed_for_both_address_families() -> None:
    """IPv6 ::1 also satisfies is_reserved, so it needs an explicit allowance."""
    assert_safe_probe_url("http://localhost:3000/health")


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A registered project directory, as the launcher's registry would report it."""
    root = tmp_path / "proj"
    (root / "worktrees" / "feature").mkdir(parents=True)
    monkeypatch.setattr(
        tasks_mod, "load_projects", lambda: {"p1": {"name": "p", "path": str(root)}}
    )
    return root


def test_launch_path_accepts_a_directory_inside_a_registered_project(project) -> None:
    wt = project / "worktrees" / "feature"
    assert _safe_launch_path(str(wt)) == str(wt.resolve())


def test_launch_path_must_be_absolute_and_exist(project) -> None:
    with pytest.raises(ValueError):
        _safe_launch_path("relative/path")
    with pytest.raises(ValueError):
        _safe_launch_path(str(project / "definitely-not-here"))


def test_launch_path_cannot_escape_the_registered_projects(project, tmp_path) -> None:
    """The endpoint must not open arbitrary directories on the host."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ValueError):
        _safe_launch_path(str(outside))
    with pytest.raises(ValueError):
        _safe_launch_path("/etc")
    # ...including via traversal, since the path is resolved before the check
    with pytest.raises(ValueError):
        _safe_launch_path(str(project / ".." / "elsewhere"))


def test_launch_path_cannot_be_option_shaped() -> None:
    """A dash-leading value is relative, so it is rejected before any registry
    lookup — no fixture needed to prove it never reaches the launcher."""
    with pytest.raises(ValueError):
        _safe_launch_path("--user-data-dir=/tmp/x")
