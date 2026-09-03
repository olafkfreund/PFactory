"""Tests for the catalogue's skill lane (the first kind: skill entries).

EntryKind has declared "skill" since the registry existed, but the catalogue
never carried one — the lane was built and unpopulated. These tests pin the
two engineering skills that now fill it, and — the part that rots — that each
entry's config.path points at a file that exists, so a moved or renamed skill
file cannot leave a dangling registry row.

Run: apps/backend/.venv/bin/pytest tests/test_registry_skill_entries.py
"""

from __future__ import annotations

from pathlib import Path

from plan.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[1]


def _skill_entries():
    return load_registry().by_kind("skill")


def test_skill_lane_is_populated() -> None:
    ids = [e.id for e in _skill_entries()]
    assert "skill:privacy-and-regulatory" in ids
    assert "skill:mobile-native" in ids


def test_every_skill_entry_points_at_an_existing_file() -> None:
    entries = _skill_entries()
    assert entries, "skill lane empty — see test_skill_lane_is_populated"
    for entry in entries:
        path = entry.config.get("path", "")
        assert path, f"{entry.id} has no config.path"
        assert (REPO_ROOT / path).is_file(), f"{entry.id} points at missing file {path}"


def test_skill_files_carry_no_emoji_directive() -> None:
    """House rule: no emojis in markdown, and no copy of the emoji-opener
    convention that backend-engineering.md carries."""
    for entry in _skill_entries():
        text = (REPO_ROOT / entry.config["path"]).read_text(encoding="utf-8")
        assert "emoji" not in text.lower(), entry.id
        assert not any(ord(ch) > 0x1F000 for ch in text), f"emoji character in {entry.id}"


def test_skill_files_are_indexed_by_the_skills_service(tmp_path) -> None:
    """The files must be discoverable where agents actually look: the
    directory-scanned skills index that feeds /api/pfactory/skills and the
    .well-known agent-skills manifest."""
    import sys  # noqa: PLC0415 - path bootstrap must precede the import

    sys.path.insert(0, str(REPO_ROOT / "apps" / "web-server"))
    try:
        from server.services.skills_service import (  # noqa: PLC0415 - importable only after the path insert
            SkillsService,
        )

        service = SkillsService(
            skills_base_path=REPO_ROOT / "skills",
            cache_path=tmp_path / "skills-cache.json",  # never touch ~/.pfactory
        )
        service.build_index()
        ids = [entry.summary.id for entries in service._index.values() for entry in entries]
        assert "engineering/privacy-and-regulatory" in ids
        assert "engineering/mobile-native" in ids
    finally:
        sys.path.remove(str(REPO_ROOT / "apps" / "web-server"))
