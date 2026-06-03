#!/usr/bin/env python3
"""Tests for #82 PR-C — Git credentials table + clone-service wiring.

Covers:
- ``_inject_credential`` rewrites HTTPS URLs and leaves SSH untouched
- ``clone_or_update(credential=...)`` passes the embedded URL to git
  and restores the sanitized origin afterwards (so the token doesn't
  end up in ``.git/config``)
- The GitCredential model imports cleanly and exposes the right
  attributes (the encrypted-at-rest column is opaque to consumers)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))


# ---------------------------------------------------------------------------
# _inject_credential
# ---------------------------------------------------------------------------


def test_inject_credential_rewrites_https():
    from server.services.project_workspace_service import _inject_credential
    out = _inject_credential(
        "https://github.com/olaf/repo.git",
        username="oauth2",
        token="ghp_secret",
    )
    assert out == "https://oauth2:ghp_secret@github.com/olaf/repo.git"


def test_inject_credential_leaves_ssh_untouched():
    """SSH URLs auth via keys, not URLs — must not be rewritten."""
    from server.services.project_workspace_service import _inject_credential
    url = "git@github.com:olaf/repo.git"
    assert _inject_credential(url, "oauth2", "secret") == url


def test_inject_credential_handles_nested_paths():
    from server.services.project_workspace_service import _inject_credential
    out = _inject_credential(
        "https://gitlab.com/group/sub/repo.git", "oauth2", "tok"
    )
    assert out == "https://oauth2:tok@gitlab.com/group/sub/repo.git"


# ---------------------------------------------------------------------------
# clone_or_update with credential
# ---------------------------------------------------------------------------


def _mock_proc(returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode

    async def _communicate():
        return (b"", b"")

    proc.communicate = _communicate
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_clone_or_update_with_credential_injects_then_sanitizes(tmp_path):
    """Fresh clone with a credential: git sees the token in the URL,
    then the sanitized origin gets set via ``git remote set-url``."""
    from server.services import project_workspace_service as svc

    captured: list[list[str]] = []

    async def fake_create_subprocess_exec(*args, **kw):
        captured.append(list(args))
        return _mock_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec):
        await svc.clone_or_update(
            git_url="https://github.com/me/private.git",
            branch="main",
            root=tmp_path,
            credential=("oauth2", "ghp_secret"),
        )

    # Find the clone command — it carries the URL with token embedded.
    clone_cmd = next(c for c in captured if c[1] == "clone")
    assert any("oauth2:ghp_secret@github.com" in arg for arg in clone_cmd), (
        f"Clone URL must carry the credential; got {clone_cmd}"
    )

    # After clone, ``git remote set-url`` must restore the sanitized URL.
    sanitize_cmd = next(
        (c for c in captured if c[1] == "remote" and c[2] == "set-url"),
        None,
    )
    assert sanitize_cmd is not None, "Origin must be sanitized post-clone"
    assert sanitize_cmd[-1] == "https://github.com/me/private.git"
    assert "ghp_secret" not in sanitize_cmd[-1], "Sanitized URL leaked the token"


@pytest.mark.asyncio
async def test_clone_or_update_existing_dir_sets_origin_for_fetch(tmp_path):
    """Update path (clone dir already exists): origin is rewritten to
    include the token for the fetch+pull, then restored afterwards."""
    from server.services import project_workspace_service as svc

    workspace = tmp_path / "me-private"
    (workspace / ".git").mkdir(parents=True)

    captured: list[list[str]] = []

    async def fake_create_subprocess_exec(*args, **kw):
        captured.append(list(args))
        return _mock_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec):
        await svc.clone_or_update(
            git_url="https://github.com/me/private.git",
            root=tmp_path,
            credential=("oauth2", "ghp_secret"),
        )

    cmd_names = [c[1] for c in captured]
    assert cmd_names.count("clone") == 0  # update path, not fresh clone
    assert "fetch" in cmd_names
    assert "pull" in cmd_names
    # Two ``remote set-url`` calls: one to inject, one to sanitize.
    set_url_calls = [c for c in captured if c[1:3] == ["remote", "set-url"]]
    assert len(set_url_calls) == 2
    # Final call restores the sanitized URL.
    assert set_url_calls[-1][-1] == "https://github.com/me/private.git"
    assert "ghp_secret" not in set_url_calls[-1][-1]


@pytest.mark.asyncio
async def test_clone_or_update_without_credential_unchanged(tmp_path):
    """Backward compat — calling without ``credential`` produces no
    ``remote set-url`` calls (no token injection happened)."""
    from server.services import project_workspace_service as svc

    captured: list[list[str]] = []

    async def fake_create_subprocess_exec(*args, **kw):
        captured.append(list(args))
        return _mock_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec):
        await svc.clone_or_update(
            git_url="https://github.com/me/public.git",
            root=tmp_path,
        )

    set_url_calls = [c for c in captured if c[1:3] == ["remote", "set-url"]]
    assert set_url_calls == [], (
        f"No ``remote set-url`` calls expected without a credential; got {set_url_calls}"
    )


# ---------------------------------------------------------------------------
# GitCredential model
# ---------------------------------------------------------------------------


def test_git_credential_model_exports_and_attributes():
    from server.database import GitCredential
    # Required column declarations (Mapped attributes appear as columns
    # on the SQLAlchemy table after Base scans the subclass).
    cols = GitCredential.__table__.columns
    assert "id" in cols
    assert "org_id" in cols
    assert "name" in cols
    assert "kind" in cols
    assert "token" in cols
    assert "host" in cols
    assert "username" in cols
    assert "created_by" in cols
    assert "created_at" in cols
    assert "last_used_at" in cols
    # The token column must be the encrypted (LargeBinary) variant — the
    # EncryptedString TypeDecorator's impl is LargeBinary.
    from sqlalchemy import LargeBinary
    token_col = cols["token"]
    assert isinstance(
        token_col.type, LargeBinary
    ) or hasattr(token_col.type, "impl"), (
        f"token column must be encrypted-at-rest; got {token_col.type!r}"
    )
