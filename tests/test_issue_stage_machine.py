"""Security checks for base policy and bound stage approvals (#1096)."""

import hashlib
import hmac
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from client_errors import InputRejectedError
from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_emit import emit_contract
from plan.emit.signing import _signing_bytes
from plan.models import Criterion, NormalizedPlan
from plan.recon.clone import CloneResult
from plan.review.models import LensScore, PlanReview
from plan.service import PlanService
from plan.stage_machine import (
    advance_stage_on_pr,
    assert_safe_git_ref,
    bootstrap_intent_pr,
    render_stage_intent,
    render_stage_plan,
    render_stage_spec,
    sync_stage_label,
)
from plan.stage_policy import (
    StageJobState,
    StagePolicy,
    load_stage_policy,
    parse_approval,
    provider_user_id,
    verify_bound_approval,
)
from tests.conftest import persist
from tests.fake_session_store import FakeSessionStore


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


@dataclass
class StageProviderFake:
    applied_labels: list[tuple[str, list[str]]] = field(default_factory=list)
    removed_labels: list[tuple[str, list[str]]] = field(default_factory=list)
    comments: list[tuple[str, str]] = field(default_factory=list)
    created_prs: list[dict] = field(default_factory=list)

    async def get_default_branch(self) -> str:
        return "dev"

    async def apply_labels(self, number: str, labels: list[str]) -> None:
        self.applied_labels.append((str(number), list(labels)))

    async def remove_labels(self, number: str, labels: list[str]) -> None:
        self.removed_labels.append((str(number), list(labels)))

    async def add_comment(self, number: str, body: str) -> int:
        self.comments.append((str(number), body))
        return len(self.comments)

    async def create_pr(
        self,
        source_branch: str,
        target_branch: str,
        title: str,
        body: str = "",
        draft: bool = False,
    ) -> dict:
        record = {
            "number": 42,
            "web_url": "https://gitlab.example/org/repo/-/merge_requests/42",
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "body": body,
            "draft": draft,
        }
        self.created_prs.append(record)
        return record

    async def merge_pr(self, *_args, **_kwargs) -> bool:
        raise AssertionError("merge_pr must never be called")

    async def enable_auto_merge(self, *_args, **_kwargs) -> bool:
        raise AssertionError("enable_auto_merge must never be called")


class FakeGitWorkspace:
    """In-memory git runner backing clone/checkout/commit/push without network."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.commits: list[str] = ["base-sha-0001"]
        self.commands: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def __call__(
        self, args: list[str], *, cwd: str, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(args))
        self.envs.append(dict(env))
        if args[0] == "clone":
            work = Path(args[-1])
            work.mkdir(parents=True, exist_ok=True)
            for rel, content in self.files.items():
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, f"{self.commits[-1]}\n", "")
        if "commit" in args:
            work = Path(cwd)
            for stage in ("intent", "spec", "plan"):
                stage_dir = work / stage
                if stage_dir.is_dir():
                    for md in stage_dir.glob("*.md"):
                        self.files[f"{stage}/{md.name}"] = md.read_text(encoding="utf-8")
            self.commits.append(f"sha-{len(self.commits):04d}")
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")


def _make_fake_session():
    plan = NormalizedPlan(
        plan_id="1096-stage-machine",
        title="Add draft PR stage machine",
        description="Drive intent, spec, and plan on a draft PR.",
        source_format="markdown",
        source_channel="github_issue",
        criteria=[Criterion(id="AC#1", text="Open draft PR with intent")],
    ).with_hash()
    epic = EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        summary="Staged PR workflow.",
        children=[
            ChildIssue(
                key="C1",
                title="Implement stage driver",
                body="Commit intent, spec, plan on draft PR.",
            )
        ],
    )

    @dataclass
    class FakeSession:
        plan: NormalizedPlan
        epic: EpicPlan
        origin_issue_number: int = 1096

    return FakeSession(plan=plan, epic=epic)


@pytest.mark.asyncio
async def test_bootstrap_intent_pr_and_advance_stage_full_cycle(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_secret_do_not_embed_in_url")
    session = _make_fake_session()
    state = StageJobState(
        tenant_id="default",
        provider="github",
        repo="org/repo",
        issue_number=1096,
        base_ref="dev",
    )
    provider = StageProviderFake()
    git = FakeGitWorkspace()
    gh_calls: list[list[str]] = []

    def fake_gh(
        argv: list[str], _cwd: str, _env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        gh_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "https://github.com/org/repo/pull/77\n", "")

    await bootstrap_intent_pr(
        state,
        provider,
        session,
        date_prefix="2026-11-20",
        git_runner=git,
        gh_runner=fake_gh,
    )

    slug = "2026-11-20-1096-add-draft-pr-stage-machine"
    assert state.source_branch == "factory/1096-add-draft-pr-stage-machine"
    assert state.artifact_slug == slug
    assert (state.pr_number, state.current_stage, state.head_sha) == (77, "intent", "sha-0001")
    assert "--draft" in gh_calls[0]
    assert all("ghp_secret_do_not_embed_in_url" not in arg for arg in git.commands[0])
    assert "https://x-access-token@github.com/org/repo.git" in git.commands[0]
    assert git.envs[0].get("GIT_PASS") == "ghp_secret_do_not_embed_in_url"
    assert git.envs[0].get("GIT_ASKPASS", "").endswith("git-askpass.sh")
    assert "status: draft" in git.files[f"intent/{slug}.md"]
    assert provider.applied_labels[-1] == ("1096", ["factory:intent"])

    await advance_stage_on_pr(
        state, provider, session, verify_lanes=("unit", "lint"), git_runner=git
    )
    assert (state.current_stage, state.stage_status, state.head_sha) == (
        "spec",
        "draft",
        "sha-0002",
    )
    assert "status: approved" in git.files[f"intent/{slug}.md"]
    assert f"intent: intent/{slug}.md" in git.files[f"spec/{slug}.md"]
    assert provider.applied_labels[-1] == ("1096", ["factory:spec"])

    seen_approved_spec: list[str] = []
    await advance_stage_on_pr(
        state,
        provider,
        session,
        plan_renderer=lambda md: (
            seen_approved_spec.append(md)
            or render_stage_plan(session, slug, verify_lanes=("unit",))
        ),
        git_runner=git,
    )
    assert len(seen_approved_spec) == 1 and "status: approved" in seen_approved_spec[0]
    assert (state.current_stage, state.stage_status, state.head_sha) == (
        "plan",
        "draft",
        "sha-0003",
    )
    assert "status: approved" in git.files[f"spec/{slug}.md"]
    assert f"spec: spec/{slug}.md" in git.files[f"plan/{slug}.md"]
    assert provider.applied_labels[-1] == ("1096", ["factory:plan"])

    await advance_stage_on_pr(state, provider, session, git_runner=git)
    assert (state.current_stage, state.stage_status, state.head_sha) == (
        "approved",
        "approved",
        "sha-0004",
    )
    assert "status: approved" in git.files[f"plan/{slug}.md"]
    assert await sync_stage_label(provider, 1096, "building") == "factory:building"


@pytest.mark.asyncio
async def test_bootstrap_gitlab_uses_provider_create_pr_and_rejects_bad_refs():
    with pytest.raises(InputRejectedError):
        assert_safe_git_ref("--upload-pack=evil", "source_branch")
    with pytest.raises(InputRejectedError):
        assert_safe_git_ref("main..evil", "base_ref")

    state = StageJobState(
        tenant_id="default",
        provider="gitlab",
        repo="gitlab:org/repo",
        issue_number=55,
    )
    provider = StageProviderFake()
    git = FakeGitWorkspace()
    await bootstrap_intent_pr(
        state,
        provider,
        title="GitLab Draft Flow",
        date_prefix="2026-11-20",
        git_runner=git,
    )
    assert state.pr_number == 42
    assert provider.created_prs[0]["draft"] is True
    assert provider.created_prs[0]["target_branch"] == "dev"

    # Simulate remote branch drift before advance_stage_on_pr
    git.commits.append("unexpected-remote-push")
    with pytest.raises(ValueError, match="drifted"):
        await advance_stage_on_pr(state, provider, git_runner=git)


def test_stage_renderers_produce_required_frontmatter():
    intent_md = render_stage_intent(issue_number=12, title="Fix auth")
    spec_md = render_stage_spec(
        issue_number=12, title="Fix auth", artifact_slug="2026-11-20-12-fix-auth"
    )
    plan_md = render_stage_plan(
        issue_number=12, title="Fix auth", artifact_slug="2026-11-20-12-fix-auth"
    )
    assert "status: draft\nissue: 12\nauthor: factory" in intent_md
    assert "status: draft\nissue: 12\nintent: intent/2026-11-20-12-fix-auth.md" in spec_md
    assert "status: draft\nissue: 12\nspec: spec/2026-11-20-12-fix-auth.md" in plan_md


def test_handoff_contract_identity_is_signed_and_request_body_is_unchanged():
    class Http:
        def __init__(self):
            self.calls = []

        def post(self, url, *, params, json):
            self.calls.append((url, params, json))
            return {"taskId": "task-1"}

    handoff = {
        "project_id": "project-1",
        "provider": "github",
        "repo": "org/repo",
        "base_ref": "dev",
        "source_branch": "factory/1096-stage-machine",
        "approved_head_sha": "a" * 40,
        "pr_number": 77,
    }
    plan = NormalizedPlan(
        plan_id="1096-stage-machine",
        title="Stage machine",
        source_format="markdown",
        description="Build the stage machine",
        criteria=[Criterion(id="AC1", text="works")],
    ).with_hash()
    epic = EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        children=[ChildIssue(key="C1", title="Implement", kind="feature")],
    )
    http = Http()
    result = emit_contract(
        plan,
        epic,
        base_url="http://ai:3101",
        project_id="project-1",
        http=http,
        key="test-key",
        approval_timestamp="2026-11-20T00:00:00Z",
        dry_run=False,
        handoff={**handoff, "unrelated_stage_state": "excluded"},
    )
    assert result["ok"] and result["signed"]
    url, params, body = http.calls[0]
    assert url == "http://ai:3101/api/tasks/from-plan"
    assert set(params) == {"project_id", "title", "description"}
    assert set(body) == {"plan"}
    contract = body["plan"]
    assert "handoff" not in contract
    assert {key: contract["provenance"][key] for key in handoff} == handoff
    assert "unrelated_stage_state" not in contract["provenance"]
    approval = contract["approval"]
    signed_bytes = _signing_bytes(
        contract,
        approval["approved_by"],
        approval["approval_timestamp"],
        approval["plan_contract_version"],
    )
    assert all(f'"{key}"'.encode() in signed_bytes for key in handoff)
    assert approval["signature"] == hmac.new(b"test-key", signed_bytes, hashlib.sha256).hexdigest()


def test_service_stage_dedup_and_single_cas_transition(monkeypatch):
    class CountingStore(FakeSessionStore):
        saves = 0

        def upsert(self, *args, **kwargs):
            self.saves += 1
            return super().upsert(*args, **kwargs)

    class LiveProvider(Provider):
        def __init__(self):
            super().__init__()
            self._gh_client = self
            self.comment = Comment(
                "c1", "alice", "/approve intent", datetime.now(UTC), {"user": {"id": 7}}
            )

        async def fetch_pr(self, _number):
            return SimpleNamespace(state="open")

        async def get_pr_head_sha(self, _number):
            return "old-sha"

        async def fetch_pr_comments(self, _number, since=None):
            return [self.comment] if since is None or self.comment.created_at > since else []

        async def remove_labels(self, *_args):
            pass

        async def apply_labels(self, *_args):
            pass

    store = CountingStore()
    svc = PlanService(persist=False, session_store=store)
    session = svc.ingest_text(
        "# Fix stage\n## Acceptance Criteria\n- approve intent",
        repo="org/repo",
        provider="github",
        project_id="project-1",
        origin_issue_number=1096,
        tenant_id="tenant-1",
    )
    state = StageJobState(
        "tenant-1",
        "github",
        "org/repo",
        1096,
        project_id="project-1",
        base_ref="dev",
        artifact_slug="slug",
        pr_number=77,
        head_sha="old-sha",
        source_branch="factory/1096-stage",
        last_comment_since=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    session.stage_state = asdict(state)
    svc._save(session)
    assert (
        svc.find_active_issue_session("tenant-1", "github", "org/repo", 1096).session_id
        == session.session_id
    )
    assert svc.find_active_issue_session("other", "github", "org/repo", 1096) is None
    provider = LiveProvider()

    async def fake_advance(state, *_args, **_kwargs):
        state.current_stage = "spec"
        state.head_sha = "new-sha"
        return state

    monkeypatch.setattr(
        "plan.stage_machine.load_stage_policy",
        lambda *_args: StagePolicy("base", ("intent",), {}, ()),
    )
    monkeypatch.setattr("plan.stage_machine.advance_stage_on_pr", fake_advance)
    before = store.saves
    result = svc.poll_issue_stage(session.session_id, provider=provider)
    assert store.saves == before + 1
    assert result.stage_state["current_stage"] == "spec"
    assert result.stage_state["approvals"][0]["provider_user_id"] == 7
    assert not store.lease


def test_service_stage_bootstrap_saves_once_after_processing(monkeypatch):
    class CountingStore(FakeSessionStore):
        saves = 0

        def upsert(self, *args, **kwargs):
            self.saves += 1
            return super().upsert(*args, **kwargs)

    store = CountingStore()
    svc = PlanService(persist=False, session_store=store)
    session = svc.ingest_text(
        "# Stage machine\n## Acceptance Criteria\n- open intent PR",
        repo="org/repo",
        origin_issue_number=1096,
    )

    async def fake_process(session_id):
        return svc.get(session_id)

    async def fake_bootstrap(state, *_args):
        state.pr_number = 77
        state.head_sha = "intent-sha"
        state.source_branch = "factory/1096-stage"
        state.artifact_slug = "slug"
        return state

    monkeypatch.setattr(svc, "process_async", fake_process)
    monkeypatch.setattr(
        "plan.stage_machine.load_stage_policy",
        lambda *_args: StagePolicy("base", ("intent",), {}, (), base_branch="dev"),
    )
    monkeypatch.setattr("plan.stage_machine.bootstrap_intent_pr", fake_bootstrap)
    before = store.saves
    result = svc.bootstrap_issue_stage(session.session_id, provider=object())
    assert store.saves == before + 1
    assert (result.stage_state["pr_number"], result.stage_state["base_ref"]) == (77, "dev")
    assert (
        svc.bootstrap_issue_stage(session.session_id, provider=object()).stage_state
        == result.stage_state
    )
    assert store.saves == before + 1


def test_service_stage_plan_approval_records_provider_provenance_and_emits(monkeypatch):
    store = FakeSessionStore()
    svc = PlanService(persist=False, session_store=store)
    session = svc.ingest_text(
        "# Stage machine\n## Acceptance Criteria\n- emit contract",
        repo="org/repo",
        provider="github",
        project_id="project-1",
        origin_issue_number=1096,
    )
    session.epic = EpicPlan(
        plan_id=session.plan.plan_id,
        epic_title="Stage machine",
        children=[
            ChildIssue(
                key="C1",
                title="Build",
                kind="feature",
                acceptance_criteria=["emit contract"],
            )
        ],
    )
    session.review = PlanReview(
        plan_id=session.plan.plan_id,
        lenses=[LensScore(lens="architecture", score=1)],
        gates_passed=True,
    )
    state = StageJobState(
        "default",
        "github",
        "org/repo",
        1096,
        project_id="project-1",
        base_ref="dev",
        source_branch="factory/1096-stage",
        artifact_slug="slug",
        pr_number=77,
        current_stage="plan",
        head_sha="reviewed-sha",
        last_comment_since=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    session.stage_state = asdict(state)
    persist(svc, session)

    class ProviderForPlan(Provider):
        def __init__(self):
            super().__init__()
            self._gh_client = self

        async def fetch_pr(self, _number):
            return SimpleNamespace(state="open")

        async def get_pr_head_sha(self, _number):
            return "reviewed-sha"

        async def fetch_pr_comments(self, _number, since=None):  # noqa: ARG002
            return [
                Comment("c-plan", "alice", "/approve plan", datetime.now(UTC), {"user": {"id": 7}})
            ]

        async def remove_labels(self, *_args):
            pass

        async def apply_labels(self, *_args):
            pass

    async def fake_advance(state, *_args, **_kwargs):
        state.current_stage = "approved"
        state.stage_status = "approved"
        state.head_sha = "approved-sha"
        return state

    captured = {}

    def fake_emit(session_id, **kwargs):
        captured.update(kwargs)
        live = svc.get(session_id)
        live.contract_result = {"ok": True}
        svc._save(live)
        return live

    monkeypatch.setattr(
        "plan.stage_machine.load_stage_policy",
        lambda *_args: StagePolicy("base", ("plan",), {}, ()),
    )
    monkeypatch.setattr("plan.stage_machine.advance_stage_on_pr", fake_advance)
    monkeypatch.setattr(svc, "_emit_contract", fake_emit)
    result = svc.poll_issue_stage(session.session_id, provider=ProviderForPlan())
    approval = result.review.human_approval
    assert (
        approval.auth_kind,
        approval.provider,
        approval.provider_user_id,
        approval.username,
    ) == (
        "provider_review",
        "github",
        7,
        "alice",
    )
    assert (approval.pr_number, approval.approved_head_sha) == (77, "approved-sha")
    assert captured["dry_run"] is False and captured["require_user_approval"] is True
    assert result.stage_state["current_stage"] == "building"
    assert result.stage_state["approved_head_sha"] == "approved-sha"
