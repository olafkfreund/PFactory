"""Security checks for base policy and bound stage approvals (#1096)."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from plan.recon.clone import CloneResult
from plan.stage_policy import (
    StageJobState,
    StagePolicy,
    load_stage_policy,
    parse_approval,
    provider_user_id,
    verify_bound_approval,
)


@dataclass
class Comment:
    id: str
    author: str
    body: str
    created_at: datetime
    raw_data: dict


class Provider:
    def __init__(self, user_id=7, permission="write"):
        self.user_id = user_id
        self.permission = permission
        self._project_id = "org%2Frepo"
        self.endpoints = []

    async def api_get(self, endpoint):
        self.endpoints.append(endpoint)
        if endpoint.startswith("/users/"):
            return {"id": self.user_id, "login": "alice"}
        return {"id": self.user_id, "username": "alice", "access_level": 30}

    async def check_permissions(self, _username):
        return self.permission


def test_policy_reads_base_clone_only(monkeypatch, tmp_path: Path):
    base = tmp_path / "base"
    base.mkdir()
    (base / ".factory.yml").write_text(
        "tiers:\n  hard:\n    stages: [intent, spec, plan]\n"
        "approvers:\n  intent: ['@alice']\nverify:\n  lanes: [unit]\n"
    )
    (base / "CODEOWNERS").write_text("/intent/ @old\n/intent/*.md @bob\n")

    @contextmanager
    def fake_clone(repo, ref):
        assert (repo, ref) == ("org/repo", "dev")
        yield CloneResult(ok=True, path=base, commit="a" * 40)

    monkeypatch.setattr("plan.stage_policy.clone_for_recon", fake_clone)
    policy = load_stage_policy("org/repo", "dev")
    assert policy.base_sha == "a" * 40
    assert policy.verify_lanes == ("unit",)
    assert policy.owners_for("intent", "slug") == ({"@alice", "@bob"}, "CODEOWNERS /intent/*.md")
    assert policy.owners_for("spec", "slug") == (set(), "repository permission")


def test_codeowners_last_match_and_no_permission_fallback():
    policy = StagePolicy("sha", ("intent",), {}, (("/intent/", ("@alice",)), ("/intent/*.md", ())))
    assert policy.owners_for("intent", "slug") == (set(), "CODEOWNERS /intent/*.md")


def test_bound_approval_parser_and_immutable_id():
    now = datetime.now(UTC)
    comment = Comment("1", "alice", " /approve intent\nthanks", now, {"user": {"id": 7}})
    assert parse_approval(comment.body) == "intent"
    assert parse_approval("/approve intentional") is None
    assert provider_user_id(comment, "github") == 7
    comment.raw_data["user"]["id"] = True
    assert provider_user_id(comment, "github") is None


@pytest.mark.asyncio
async def test_bound_approval_codeowners_replay_and_sha():
    now = datetime.now(UTC)
    state = StageJobState(
        "tenant", "github", "org/repo", 1, artifact_slug="slug", pr_number=2, head_sha="abc"
    )
    policy = StagePolicy("base", ("intent",), {}, (("/intent/", ("@alice",)),))
    comment = Comment("1", "alice", "/approve intent", now, {"user": {"id": 7}})
    provider = Provider()
    assert await verify_bound_approval(
        state, policy, comment, provider, "abc", now - timedelta(seconds=1)
    )
    assert (
        await verify_bound_approval(
            state, policy, comment, provider, "abc", now - timedelta(seconds=1)
        )
        is None
    )
    assert len(state.approvals) == 1
    stale = Comment("2", "alice", "/approve intent", now, {"user": {"id": 7}})
    assert (
        await verify_bound_approval(
            state, policy, stale, provider, "new-head", now - timedelta(seconds=1)
        )
        is None
    )
    assert "2" in state.consumed_comment_ids


@pytest.mark.asyncio
async def test_bound_approval_rejects_nonowner_even_with_write_and_id_mismatch():
    now = datetime.now(UTC)
    policy = StagePolicy("base", ("intent",), {}, (("/intent/", ("@bob",)),))
    state = StageJobState(
        "tenant", "github", "org/repo", 1, artifact_slug="slug", pr_number=2, head_sha="abc"
    )
    comment = Comment("1", "alice", "/approve intent", now, {"user": {"id": 7}})
    assert (
        await verify_bound_approval(
            state, policy, comment, Provider(permission="admin"), "abc", now
        )
        is None
    )
    assert "1" in state.consumed_comment_ids
    comment.id = "2"
    assert (
        await verify_bound_approval(state, policy, comment, Provider(user_id=8), "abc", now) is None
    )


@pytest.mark.asyncio
async def test_bound_approval_gitlab_exact_member_lookup_and_fallback():
    now = datetime.now(UTC)
    state = StageJobState(
        "tenant", "gitlab", "org/repo", 1, artifact_slug="slug", pr_number=2, head_sha="abc"
    )
    policy = StagePolicy("base", ("intent",), {}, ())
    provider = Provider()
    comment = Comment("1", "alice", "/approve intent", now, {"author": {"id": 7}})
    assert await verify_bound_approval(state, policy, comment, provider, "abc", now)
    assert provider.endpoints == ["/api/v4/projects/org%2Frepo/members/all/7"]


@pytest.mark.asyncio
async def test_bound_approval_requires_fresh_comment_and_write_fallback():
    now = datetime.now(UTC)
    state = StageJobState(
        "tenant", "github", "org/repo", 1, artifact_slug="slug", pr_number=2, head_sha="abc"
    )
    policy = StagePolicy("base", ("intent",), {}, ())
    provider = Provider(permission="read")
    old = Comment("1", "alice", "/approve intent", now, {"user": {"id": 7}})
    assert (
        await verify_bound_approval(state, policy, old, provider, "abc", now + timedelta(seconds=1))
        is None
    )
    fresh = Comment(
        "2", "alice", "/approve intent", now + timedelta(seconds=2), {"user": {"id": 7}}
    )
    assert await verify_bound_approval(state, policy, fresh, provider, "abc", now) is None
    provider.permission = "write"
    fresh.id = "3"
    assert await verify_bound_approval(state, policy, fresh, provider, "abc", now)
