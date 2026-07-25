"""PFactory v0.2 portal endpoints: Claude skill bundles.

Serves the .claude/skills/ skill catalogue to the portal:
  GET /api/pfactory/skills    — list skill bundles + their frontmatter

Each skill bundle is a directory under ``.claude/skills/`` that contains a
``SKILL.md`` file. The YAML frontmatter of that file holds machine-readable
metadata (name, description, when_to_use, allowed-tools).

Graceful degradation: if ``.claude/skills/`` is absent (Task 13 may not have
merged yet when this endpoint first runs), returns ``{"skills": []}``.

Note on imports: FastAPI is only present at runtime (inside the web-server
venv). This module is unit-tested without FastAPI installed; the test suite
injects a minimal stub before importing this module.
"""

from __future__ import annotations

import json as _json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from fastapi import APIRouter, Response


router = APIRouter()
_log = logging.getLogger(__name__)


# ─── Path resolution ─────────────────────────────────────────────────────────


def _resolve_skills_dir() -> Path | None:
    """Return the ``.claude/skills/`` directory at the repo root, or None.

    This file lives at:
        <repo>/apps/web-server/server/routes/pfactory_skills.py
    parents[4] = <repo>
    """
    here = Path(__file__).resolve()
    candidate = here.parents[4] / ".claude" / "skills"
    if candidate.is_dir():
        return candidate
    return None


# ─── YAML frontmatter parser ──────────────────────────────────────────────────


def _parse_frontmatter(skill_md: Path) -> dict[str, Any] | None:
    """Parse the YAML frontmatter from a SKILL.md file.

    Returns a plain dict on success, or None if:
    - the file cannot be read
    - the file has no ``---`` frontmatter delimiter
    - the YAML is malformed

    Callers that receive None should skip / warn and continue — bad SKILL.md
    files must not crash the listing endpoint.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        _log.warning("skills endpoint: cannot read %s: %s", skill_md, exc)
        return None

    if not text.startswith("---"):
        _log.warning(
            "skills endpoint: %s has no YAML frontmatter (missing leading ---)",
            skill_md,
        )
        return None

    # Split on the closing --- delimiter
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        _log.warning(
            "skills endpoint: %s frontmatter has no closing --- delimiter",
            skill_md,
        )
        return None

    front_raw = parts[0][3:].lstrip("\n")  # strip leading "---"
    try:
        meta = yaml.safe_load(front_raw)
    except yaml.YAMLError as exc:
        _log.warning(
            "skills endpoint: malformed YAML in %s: %s", skill_md, exc
        )
        return None

    if not isinstance(meta, dict):
        _log.warning(
            "skills endpoint: frontmatter in %s is not a YAML mapping", skill_md
        )
        return None

    return meta


def _skill_row(skill_dir: Path) -> dict[str, Any] | None:
    """Build one row for the skills list from a skill bundle directory.

    Returns None if the SKILL.md is missing or its frontmatter is malformed.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None

    meta = _parse_frontmatter(skill_md)
    if meta is None:
        return None

    # Normalise `allowed-tools` vs `allowed_tools` — SKILL.md convention
    # uses the hyphenated form; we expose the underscored form in the API.
    allowed_tools = meta.get("allowed-tools") or meta.get("allowed_tools") or []
    when_to_use = meta.get("when_to_use") or meta.get("when-to-use") or ""

    return {
        "name": meta.get("name") or skill_dir.name,
        "description": meta.get("description") or "",
        "when_to_use": when_to_use,
        "allowed_tools": allowed_tools,
    }


# ─── Env override for testing ─────────────────────────────────────────────────


def _effective_skills_dir() -> Path | None:
    """Return the skills directory to use.

    Respects the ``PFACTORY_SKILLS_DIR`` env override so tests can inject
    a temporary directory without touching the real ``.claude/skills/``.
    """
    env_val = os.environ.get("PFACTORY_SKILLS_DIR")
    if env_val:
        p = Path(env_val).expanduser()
        return p if p.is_dir() else None
    return _resolve_skills_dir()


# ─── Shared collector ─────────────────────────────────────────────────────────


def collect_skills() -> list[dict[str, Any]]:
    """Return one row per well-formed skill bundle in ``.claude/skills/``.

    Shared by this route and the RFC-0019 agent-skills manifest
    (``routes/well_known.py``) so both describe the same catalogue and cannot
    drift. Never raises: a missing directory yields ``[]`` and a bundle with a
    missing/malformed SKILL.md is skipped.
    """
    skills_dir = _effective_skills_dir()
    if skills_dir is None:
        return []

    rows: list[dict[str, Any]] = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        row = _skill_row(entry)
        if row is not None:
            rows.append(row)
    return rows


# ─── Endpoint ─────────────────────────────────────────────────────────────────


@router.get("")
def list_skills() -> Response:
    """List all PFactory skill bundles from ``.claude/skills/``.

    Walks every sub-directory that contains a ``SKILL.md`` file. Parses the
    YAML frontmatter of each. Skips (and logs a warning for) any bundle whose
    SKILL.md is missing or has malformed YAML — the endpoint never crashes on
    bad data.

    If ``.claude/skills/`` does not exist (Task 13 not yet merged), returns
    an empty list gracefully.

    Response shape::

        {
          "skills": [
            {
              "name": "handover-to-pfactory",
              "description": "Hand a finished AIFactory spec off to PFactory ...",
              "when_to_use": "When the user has finished ...",
              "allowed_tools": ["mcp__pfactory__task_create_and_run", ...]
            }
          ]
        }
    """
    payload = {"skills": collect_skills()}
    return Response(
        content=_json.dumps(payload),
        media_type="application/json",
        status_code=200,
    )
