"""A clone-mode ``gitUrl`` must never register a path outside the workspace root.

``slug_from_git_url`` maps a URL path to a directory name with::

    re.sub(r"[^A-Za-z0-9._-]+", "-", path)

``.`` is inside the permitted class, so ``..`` survives the substitution intact:
``https://host/..`` slugs to ``".."``, which names the PARENT of the workspace
root once joined. A second ``..`` needs a ``/``, which the regex hyphenates, so
the escape is bounded to one level -- but one level up from
``~/.pfactory/workspaces`` is ``~/.tfactory``, the directory holding the
project registry and the credential store.

Two independent halves have to hold, and these tests pin them separately so
neither can rot behind the other:

1. ``slug_from_git_url`` itself refuses to return a traversing component, so
   every caller (present and future) gets a real path component.
2. ``clone_or_update`` re-checks the component it is about to join, so the
   registry invariant survives even if the slug function is changed -- this
   half is what covers the explicit ``slug=`` override, which never passes
   through ``slug_from_git_url`` at all.

Test 2 deliberately uses the ``slug=`` override so it exercises ONLY half 2;
test 1 uses the URL so it exercises ONLY half 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_WEB_SERVER = Path(__file__).resolve().parents[1]
_BACKEND = _WEB_SERVER.parent / "backend"
for _p in (str(_WEB_SERVER), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from server.routes import projects as projects_route  # noqa: E402
from server.services import (  # noqa: E402
    project_paths,
    project_workspace_service as pws,
)

# URLs whose path component slugs to a traversing name. ``git@host:..`` covers
# the scp-like branch, which splits on the colon instead of using urlparse.
TRAVERSAL_URLS = [
    "https://host/..",
    "https://host/../",
    "https://host/.",
    "git@host:..",
]


# ─── half 1: the slug function refuses to emit a traversing component ────────


@pytest.mark.parametrize("url", TRAVERSAL_URLS)
def test_slug_from_git_url_refuses_traversal(url):
    """Reject, not sanitise: a silently-rewritten slug would register the
    project under a directory name the caller never asked for."""
    with pytest.raises(ValueError):
        pws.slug_from_git_url(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://host/repo", "repo"),
        ("https://github.com/olaf/PFactory.git", "olaf-PFactory"),
        ("git@github.com:olaf/PFactory.git", "olaf-PFactory"),
        ("https://gitlab.com/group/sub/repo.git", "group-sub-repo"),
        # A dot inside a real repo name is legitimate and must survive intact --
        # a fix that stripped "." from the charset would break these.
        ("https://host/me/repo.js", "me-repo.js"),
        ("https://host", "workspace"),
    ],
)
def test_slug_from_git_url_still_accepts_real_urls(url, expected):
    assert pws.slug_from_git_url(url) == expected


# ─── half 2: clone_or_update re-checks the component before joining ──────────


@pytest.mark.asyncio
async def test_clone_or_update_refuses_traversing_slug_override(tmp_path):
    """The explicit ``slug=`` override never passes through
    ``slug_from_git_url``, so only the barrier inside ``clone_or_update``
    can catch it. Assert nothing is created outside the root."""
    root = tmp_path / "workspaces"
    root.mkdir()
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()

    with pytest.raises(ValueError):
        await pws.clone_or_update(git_url="https://host/repo", slug="..", root=root)
    # The parent of the root is untouched -- no clone landed there.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sentinel", "workspaces"]
    assert list(root.iterdir()) == []


# ─── end to end: what actually lands in the registry ─────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient over the projects router with the workspace root and the
    registry file both redirected into tmp_path, and git stubbed out."""
    root = tmp_path / "workspaces"
    root.mkdir()
    registry = tmp_path / "projects.json"
    monkeypatch.setenv("PROJECT_WORKSPACE_ROOT", str(root))
    monkeypatch.setattr(project_paths, "get_projects_file", lambda: registry)

    def _mkdir(dest: str) -> None:
        # Split out of the async stub below: ASYNC240 bans pathlib inside an
        # `async def`, and PTH103 bans the os.makedirs alternative. A sync
        # helper satisfies both.
        Path(dest).mkdir(parents=True, exist_ok=True)

    async def fake_create_subprocess_exec(*args, **_kw):
        # Emulate `git clone <url> <dest>`: create the destination.
        if "clone" in args:
            _mkdir(args[-1])

        class _Proc:
            returncode = 0

            async def communicate(self):
                return (b"", b"")

        return _Proc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    app = FastAPI()
    app.include_router(projects_route.router, prefix="/api/projects")
    with TestClient(app) as c:
        yield c, root, registry


def test_clone_mode_traversal_url_registers_nothing(client):
    c, root, registry = client
    resp = c.post("/api/projects", json={"gitUrl": "https://host/.."})

    # 400, not 500: the barrier's ValueError is translated at the route.
    assert resp.status_code == 400, resp.text
    # Where did the path land? Nowhere: no registry file, no directory above
    # the workspace root.
    assert not registry.exists() or projects_route.load_projects() == {}
    assert list(root.iterdir()) == []
    assert not (root.parent / ".git").exists()


def test_clone_mode_normal_url_registers_inside_the_workspace_root(client):
    """The fix must not break legitimate cloning."""
    c, root, _registry = client
    resp = c.post("/api/projects", json={"gitUrl": "https://host/repo"})

    assert resp.status_code == 201, resp.text
    registered = Path(resp.json()["path"])
    assert registered == (root / "repo").resolve()
    # Contained: the registered path is strictly under the workspace root.
    assert registered.is_relative_to(root.resolve())
