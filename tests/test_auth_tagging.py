"""Tests for deterministic requires_auth tagging (#107 task 6).

Backend-pure: config is a duck-typed fake; load_pfactory_yml is monkeypatched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agents.auth_tagging import apply_requires_auth_from_config, tag_requires_auth
from test_plan.subtask import Subtask


def _fake_config(targets: dict[str, str | None]):
    """Build a config whose lookup_target maps name → a target with that auth type.

    ``targets`` maps target name → auth ``type`` string (e.g. "ref" / "bearer"),
    or ``None`` for a target with no auth. Unknown names resolve to ``None``.
    """

    def lookup(name):
        if name not in targets:
            return None
        at = targets[name]
        auth = SimpleNamespace(type=at) if at is not None else None
        return SimpleNamespace(name=name, auth=auth)

    return SimpleNamespace(lookup_target=lookup)


def _subtask(sid: str, target_name: str | None, requires_auth: bool = False) -> Subtask:
    return Subtask(
        id=sid, description="x", target_name=target_name, requires_auth=requires_auth
    )


# ── tag_requires_auth ────────────────────────────────────────────────────────


def test_tags_ref_auth_target() -> None:
    st = _subtask("t1", "app")
    n = tag_requires_auth([st], _fake_config({"app": "ref"}))
    assert n == 1
    assert st.requires_auth is True


def test_leaves_non_ref_auth_untouched() -> None:
    st = _subtask("t1", "api")
    n = tag_requires_auth([st], _fake_config({"api": "bearer"}))
    assert n == 0
    assert st.requires_auth is False


def test_skips_already_tagged() -> None:
    st = _subtask("t1", "app", requires_auth=True)
    n = tag_requires_auth([st], _fake_config({"app": "ref"}))
    assert n == 0  # already True — not re-counted


def test_skips_subtask_without_target_name() -> None:
    st = _subtask("t1", None)
    assert tag_requires_auth([st], _fake_config({"app": "ref"})) == 0


def test_skips_unknown_target() -> None:
    st = _subtask("t1", "ghost")
    assert tag_requires_auth([st], _fake_config({"app": "ref"})) == 0
    assert st.requires_auth is False


def test_none_config_tags_nothing() -> None:
    st = _subtask("t1", "app")
    assert tag_requires_auth([st], None) == 0


def test_mixed_batch_counts_only_ref() -> None:
    subs = [
        _subtask("t1", "app"),       # ref → tag
        _subtask("t2", "api"),       # bearer → no
        _subtask("t3", "app"),       # ref → tag
        _subtask("t4", None),        # no target → no
    ]
    n = tag_requires_auth(subs, _fake_config({"app": "ref", "api": "bearer"}))
    assert n == 2
    assert [s.requires_auth for s in subs] == [True, False, True, False]


# ── apply_requires_auth_from_config ──────────────────────────────────────────


def _plan(*subtasks):
    return SimpleNamespace(phases=[SimpleNamespace(subtasks=list(subtasks))])


def test_apply_loads_config_and_tags(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import pfactory_yml.parser as parser

    monkeypatch.setattr(parser, "load_pfactory_yml", lambda root: _fake_config({"app": "ref"}))
    st = _subtask("t1", "app")
    n = apply_requires_auth_from_config(_plan(st), tmp_path)
    assert n == 1
    assert st.requires_auth is True


def test_apply_none_project_dir_is_zero() -> None:
    st = _subtask("t1", "app")
    assert apply_requires_auth_from_config(_plan(st), None) == 0


def test_apply_no_config_file_is_zero(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import pfactory_yml.parser as parser

    monkeypatch.setattr(parser, "load_pfactory_yml", lambda root: None)
    assert apply_requires_auth_from_config(_plan(_subtask("t1", "app")), tmp_path) == 0


def test_apply_swallows_config_errors(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import pfactory_yml.parser as parser

    def _boom(root):
        raise ValueError("malformed .pfactory.yml")

    monkeypatch.setattr(parser, "load_pfactory_yml", _boom)
    # Best-effort: a config error must never break planning.
    assert apply_requires_auth_from_config(_plan(_subtask("t1", "app")), tmp_path) == 0
