"""Tests for /api/pfactory/skills portal endpoint — Task 14 (#30).

Tests the route function in
``apps/web-server/server/routes/pfactory_skills.py`` directly.
FastAPI is stubbed out via sys.modules injection.

Covered:
  - list_skills: returns {"skills": []} when .claude/skills/ dir is absent
  - list_skills: returns parsed frontmatter for 2 synthetic SKILL.md files
  - list_skills: skips malformed SKILL.md without crashing
  - list_skills: response shape is exactly {"skills": [...]}
  - list_skills: allowed-tools vs allowed_tools normalisation
  - list_skills: skips directories with no SKILL.md file
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

# ── FastAPI stub ─────────────────────────────────────────────────────────────
if "fastapi" not in sys.modules:
    _fastapi = types.ModuleType("fastapi")

    class _APIRouter:
        def __init__(self, *a, **kw): pass
        def get(self, *a, **kw):
            def _d(fn): return fn
            return _d
        def websocket(self, *a, **kw):
            def _d(fn): return fn
            return _d

    class _HTTPException(Exception):
        def __init__(self, status_code: int, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _Response:
        def __init__(self, content=b"", media_type: str = "", status_code: int = 200) -> None:
            self.content = (
                content if isinstance(content, (bytes, bytearray))
                else str(content).encode()
            )
            self.media_type = media_type
            self.status_code = status_code
            self.body = self.content

    class _WebSocket:
        async def accept(self): pass
        async def send_text(self, _t: str): pass
        async def receive_text(self) -> str: return ""
        async def close(self, code: int = 1000, reason: str = ""): pass

    class _WebSocketDisconnect(Exception):
        pass

    _status = types.ModuleType("fastapi.status")
    _status.HTTP_400_BAD_REQUEST = 400
    _status.HTTP_404_NOT_FOUND = 404
    _status.HTTP_500_INTERNAL_SERVER_ERROR = 500

    _fastapi.APIRouter = _APIRouter
    _fastapi.HTTPException = _HTTPException
    _fastapi.Response = _Response
    _fastapi.WebSocket = _WebSocket
    _fastapi.WebSocketDisconnect = _WebSocketDisconnect
    _fastapi.status = _status
    sys.modules["fastapi"] = _fastapi
    sys.modules["fastapi.status"] = _status


# ── sys.path setup ───────────────────────────────────────────────────────────
WEB_SERVER_PATH = Path(__file__).parent.parent / "apps" / "web-server"
for _p in (str(WEB_SERVER_PATH),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


from server.routes.pfactory_skills import list_skills  # noqa: E402

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def skills_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a tmp .claude/skills/ tree and point PFACTORY_SKILLS_DIR at it."""
    sdir = tmp_path / ".claude" / "skills"
    sdir.mkdir(parents=True)
    monkeypatch.setenv("PFACTORY_SKILLS_DIR", str(sdir))
    return sdir


def _write_skill(
    skills_dir: Path,
    name: str,
    *,
    description: str = "A test skill.",
    when_to_use: str = "When you want to test.",
    allowed_tools: list[str] | None = None,
) -> Path:
    """Write a minimal well-formed SKILL.md into skills_dir/<name>/."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    tools = allowed_tools or []
    tools_yaml = "\n".join(f"  - {t}" for t in tools)
    content = f"""---
name: {name}
description: {description}
when_to_use: {when_to_use}
allowed-tools:
{tools_yaml if tools_yaml else "  []"}
---

# /{name}

Skill body here.
"""
    (skill_dir / "SKILL.md").write_text(content)
    return skill_dir


# ── Tests: empty / absent skills dir ─────────────────────────────────────────


def test_list_skills_empty_when_dir_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the skills dir does not exist, return {"skills": []}."""
    monkeypatch.setenv("PFACTORY_SKILLS_DIR", str(tmp_path / "nonexistent"))
    resp = list_skills()
    payload = json.loads(resp.body)
    assert payload == {"skills": []}


def test_list_skills_empty_when_skills_dir_is_empty(
    skills_dir: Path,
) -> None:
    """Empty dir → empty list."""
    resp = list_skills()
    payload = json.loads(resp.body)
    assert payload == {"skills": []}


# ── Tests: parsed frontmatter ─────────────────────────────────────────────────


def test_list_skills_returns_parsed_frontmatter(skills_dir: Path) -> None:
    """Two synthetic SKILL.md files → both returned with frontmatter parsed."""
    _write_skill(
        skills_dir, "skill-alpha",
        description="Alpha skill",
        when_to_use="When A is needed",
        allowed_tools=["Bash", "mcp__some__tool"],
    )
    _write_skill(
        skills_dir, "skill-beta",
        description="Beta skill",
        when_to_use="When B is needed",
        allowed_tools=["mcp__beta__tool"],
    )

    resp = list_skills()
    payload = json.loads(resp.body)
    skills = payload["skills"]
    assert len(skills) == 2

    names = {s["name"] for s in skills}
    assert "skill-alpha" in names
    assert "skill-beta" in names

    alpha = next(s for s in skills if s["name"] == "skill-alpha")
    assert alpha["description"] == "Alpha skill"
    assert alpha["when_to_use"] == "When A is needed"
    assert "Bash" in alpha["allowed_tools"]
    assert "mcp__some__tool" in alpha["allowed_tools"]


def test_list_skills_single_skill(skills_dir: Path) -> None:
    _write_skill(skills_dir, "my-skill", description="Solo skill.")
    resp = list_skills()
    payload = json.loads(resp.body)
    assert len(payload["skills"]) == 1
    assert payload["skills"][0]["name"] == "my-skill"
    assert payload["skills"][0]["description"] == "Solo skill."


# ── Tests: malformed SKILL.md ─────────────────────────────────────────────────


def test_list_skills_skips_malformed_skill_md(skills_dir: Path) -> None:
    """One valid + one YAML-broken SKILL.md → only the valid one returned."""
    _write_skill(skills_dir, "good-skill", description="Works fine.")

    broken_dir = skills_dir / "broken-skill"
    broken_dir.mkdir()
    (broken_dir / "SKILL.md").write_text("---\n{not: valid: yaml: [\n---\nBody\n")

    resp = list_skills()
    payload = json.loads(resp.body)
    # Only the good skill should appear
    assert len(payload["skills"]) == 1
    assert payload["skills"][0]["name"] == "good-skill"


def test_list_skills_skips_skill_dir_without_skill_md(skills_dir: Path) -> None:
    """A directory without SKILL.md is silently skipped."""
    _write_skill(skills_dir, "real-skill")
    no_md_dir = skills_dir / "no-skill-md"
    no_md_dir.mkdir()
    (no_md_dir / "README.md").write_text("not a SKILL.md")

    resp = list_skills()
    payload = json.loads(resp.body)
    assert len(payload["skills"]) == 1


def test_list_skills_skips_missing_frontmatter_delimiter(skills_dir: Path) -> None:
    """A SKILL.md without --- delimiter is skipped without crash."""
    _write_skill(skills_dir, "good-skill")
    no_fm = skills_dir / "no-frontmatter"
    no_fm.mkdir()
    (no_fm / "SKILL.md").write_text("name: no-frontmatter\nNo leading --- delimiter\n")

    resp = list_skills()
    payload = json.loads(resp.body)
    assert len(payload["skills"]) == 1


# ── Tests: response shape ─────────────────────────────────────────────────────


def test_list_skills_response_shape(skills_dir: Path) -> None:
    """Response is exactly {"skills": [...]} — no extra top-level keys."""
    _write_skill(skills_dir, "shape-skill")
    resp = list_skills()
    payload = json.loads(resp.body)
    assert set(payload.keys()) == {"skills"}
    assert isinstance(payload["skills"], list)


def test_list_skills_each_row_has_required_fields(skills_dir: Path) -> None:
    """Each skill row exposes name, description, when_to_use, allowed_tools."""
    _write_skill(skills_dir, "field-skill")
    resp = list_skills()
    payload = json.loads(resp.body)
    for row in payload["skills"]:
        assert "name" in row
        assert "description" in row
        assert "when_to_use" in row
        assert "allowed_tools" in row


def test_list_skills_allowed_tools_normalised_to_underscore(skills_dir: Path) -> None:
    """SKILL.md uses 'allowed-tools'; API response exposes 'allowed_tools'."""
    _write_skill(skills_dir, "norm-skill", allowed_tools=["mcp__foo__bar"])
    resp = list_skills()
    payload = json.loads(resp.body)
    row = payload["skills"][0]
    assert "allowed_tools" in row
    assert "allowed-tools" not in row


def test_list_skills_response_is_json(skills_dir: Path) -> None:
    _write_skill(skills_dir, "json-skill")
    resp = list_skills()
    assert resp.media_type == "application/json"
    assert resp.status_code == 200
