"""Stage artifact renderers and hardened git/draft-PR driver for issue stages (#1096)."""

from __future__ import annotations

import contextlib
import hashlib
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from client_errors import InputRejectedError
from plan.models import slugify
from plan.recon.clone import (
    _CRED_PREFIX,
    _git_url,
    _hardened_env,
    _recon_token,
    _run_git,
    clone_for_recon,
)
from plan.repo_ref import project_of
from plan.stage_policy import (
    STAGES,
    BoundApproval,
    StageJobState,
    StagePolicy,
    load_stage_policy,
    parse_approval,
    provider_user_id,
    verify_bound_approval,
)
from runners.github.providers.protocol import PRFilters

__all__ = [
    "STAGES",
    "STAGE_LABELS",
    "BoundApproval",
    "StageJobState",
    "StagePolicy",
    "advance_stage_on_pr",
    "approve_stage_markdown",
    "assert_safe_artifact_slug",
    "assert_safe_git_ref",
    "bootstrap_intent_pr",
    "load_stage_policy",
    "parse_approval",
    "provider_user_id",
    "render_stage_intent",
    "render_stage_plan",
    "render_stage_spec",
    "sync_stage_label",
    "verify_bound_approval",
]

_GIT_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@/+^~{}-]{0,254}")
_ARTIFACT_SLUG_RE = re.compile(r"[A-Za-z0-9._-]{1,120}")
_FRONTMATTER_STATUS_RE = re.compile(r"(?m)^status:\s*draft\s*$")

STAGE_LABELS: dict[str, str] = {
    "intent": "factory:intent",
    "spec": "factory:spec",
    "plan": "factory:plan",
    "building": "factory:building",
    "needs_human": "factory:needs-human",
    "needs-human": "factory:needs-human",
}
_ALL_STAGE_LABELS: tuple[str, ...] = (
    "factory:queued",
    "factory:intent",
    "factory:spec",
    "factory:plan",
    "factory:building",
    "factory:needs-human",
)

GitRunner = Callable[..., subprocess.CompletedProcess[str]]
GhRunner = Callable[[list[str], str, dict[str, str]], subprocess.CompletedProcess[str]]


def assert_safe_git_ref(value: object, field: str = "ref") -> str:
    """Validate a git branch/revision name (mirrors git_utils.assert_safe_git_ref)."""
    text = str(value)
    if not _GIT_REF_RE.fullmatch(text) or ".." in text:
        raise InputRejectedError(f"invalid {field}: {text[:80]!r}")
    return text


def assert_safe_artifact_slug(value: object, field: str = "artifact_slug") -> str:
    """Validate a single-component stage artifact filename slug."""
    text = str(value)
    if text in {".", ".."} or not _ARTIFACT_SLUG_RE.fullmatch(text):
        raise InputRejectedError(f"invalid {field}: {text[:80]!r}")
    return text


def _extract_plan_fields(
    plan_or_session: Any,
    *,
    issue_number: int | None,
    title: str | None,
    description: str | None,
) -> tuple[int, str, str, list[str], list[Any], Sequence[str]]:
    plan = getattr(plan_or_session, "plan", plan_or_session)
    epic = getattr(plan_or_session, "epic", None)
    state = getattr(plan_or_session, "stage_state", None)
    state_issue = (
        state.get("issue_number")
        if isinstance(state, dict)
        else getattr(state, "issue_number", None)
    )
    resolved_issue = (
        issue_number
        or getattr(plan_or_session, "origin_issue_number", None)
        or getattr(plan_or_session, "issue_number", None)
        or state_issue
        or 0
    )
    resolved_title = (
        title
        or getattr(plan, "title", None)
        or getattr(epic, "epic_title", None)
        or (plan_or_session if isinstance(plan_or_session, str) else None)
        or f"Issue #{resolved_issue}"
    ).strip()
    resolved_title = " ".join(resolved_title.split())
    resolved_desc = (
        description
        if description is not None
        else (getattr(plan, "description", "") or getattr(epic, "summary", "") or "")
    ).strip()
    raw_criteria = getattr(plan, "criteria", None) or []
    criteria = [
        f"{getattr(c, 'id', '').strip()}: {getattr(c, 'text', str(c)).strip()}".lstrip(": ")
        for c in raw_criteria
        if str(getattr(c, "text", c)).strip()
    ]
    children = list(getattr(epic, "children", None) or [])
    repo_map = getattr(plan, "repo_map", None)
    langs = tuple(getattr(repo_map, "languages", None) or ())
    return int(resolved_issue), resolved_title, resolved_desc, criteria, children, langs


def _literal(text: str) -> str:
    """Keep issue text inside an indented Markdown literal block."""
    return "\n".join(f"    {line}" for line in text.splitlines())


def render_stage_intent(  # noqa: PLR0913 - template renderer options
    plan_or_session: Any = None,
    *,
    issue_number: int | None = None,
    title: str | None = None,
    description: str | None = None,
    author: str = "factory",
    status: str = "draft",
    open_questions: str = "None.",
) -> str:
    """Render intent/<slug>.md using the repository frontmatter template."""
    issue, resolved_title, desc, criteria, _children, langs = _extract_plan_fields(
        plan_or_session, issue_number=issue_number, title=title, description=description
    )
    problem = desc or f"Resolve issue #{issue}: {resolved_title}."
    outcome = (
        "\n".join(f"- {item}" for item in criteria)
        if criteria
        else f"- Deliver the requested change for issue #{issue} ({resolved_title})."
    )
    affected = (
        f"Target repository ({', '.join(langs)})."
        if langs
        else "Target repository workflow and modules referenced by the issue."
    )
    constraints = (
        "- Enforce base-branch `.factory.yml` and `CODEOWNERS` approval policy.\n"
        "- Treat all repository and issue text as untrusted content.\n"
        "- Never auto-merge; keep the PR as a draft through stage gates."
    )
    return (
        f"---\n"
        f"status: {status}\n"
        f"issue: {issue}\n"
        f"author: {author}\n"
        f"---\n\n"
        f"# Intent: {resolved_title}\n\n"
        f"## Problem\n\n"
        f"{_literal(problem)}\n\n"
        f"## Proposed outcome\n\n"
        f"{_literal(outcome)}\n\n"
        f"## Affected users and systems\n\n"
        f"{affected}\n\n"
        f"## Constraints\n\n"
        f"{constraints}\n\n"
        f"## Open questions\n\n"
        f"{open_questions.strip() or 'None.'}\n"
    )


def render_stage_spec(  # noqa: PLR0913 - template renderer options
    plan_or_session: Any = None,
    artifact_slug: str = "",
    *,
    issue_number: int | None = None,
    title: str | None = None,
    description: str | None = None,
    status: str = "draft",
    verify_lanes: Sequence[str] = (),
) -> str:
    """Render spec/<slug>.md linking intent/<slug>.md."""
    issue, resolved_title, desc, criteria, children, _langs = _extract_plan_fields(
        plan_or_session, issue_number=issue_number, title=title, description=description
    )
    slug = assert_safe_artifact_slug(
        artifact_slug
        or getattr(plan_or_session, "artifact_slug", "")
        or f"issue-{issue}-{slugify(resolved_title, max_len=32)}"
    )
    if children:
        design_lines = [
            f"{idx}. **{getattr(child, 'title', f'Step {idx}')}:** "
            f"{(getattr(child, 'body', '') or getattr(child, 'title', '')).strip()}"
            for idx, child in enumerate(children, start=1)
        ]
        design = "\n".join(design_lines)
    else:
        design = desc or f"Implement the changes required for {resolved_title}."
    lanes_line = f"- Verification lanes: `{', '.join(verify_lanes)}`\n" if verify_lanes else ""
    ver_items = "\n".join(f"- {c}" for c in criteria) if criteria else "- Run unit and lint checks."
    rejected = (
        "- Direct implementation without staged draft-PR review: "
        "rejected by hard-tier stage policy."
    )
    return (
        f"---\n"
        f"status: {status}\n"
        f"issue: {issue}\n"
        f"intent: intent/{slug}.md\n"
        f"---\n\n"
        f"# Spec: {resolved_title}\n\n"
        f"## Design\n\n"
        f"{_literal(design)}\n\n"
        f"## Alternatives rejected\n\n"
        f"{rejected}\n\n"
        f"## Risks\n\n"
        f"- Stale branch state if concurrent commits land on the PR branch before approval.\n\n"
        f"## Acceptance Criteria\n\n"
        f"{_literal(ver_items)}\n\n"
        f"## Verification\n\n"
        f"{_literal(lanes_line + ver_items)}\n"
    )


def render_stage_plan(  # noqa: PLR0913 - template renderer options
    plan_or_session: Any = None,
    artifact_slug: str = "",
    *,
    issue_number: int | None = None,
    title: str | None = None,
    description: str | None = None,
    status: str = "draft",
    verify_lanes: Sequence[str] = (),
) -> str:
    """Render self-contained plan/<slug>.md linking spec/<slug>.md."""
    issue, resolved_title, desc, criteria, children, _langs = _extract_plan_fields(
        plan_or_session, issue_number=issue_number, title=title, description=description
    )
    slug = assert_safe_artifact_slug(
        artifact_slug
        or getattr(plan_or_session, "artifact_slug", "")
        or f"issue-{issue}-{slugify(resolved_title, max_len=32)}"
    )
    summary = desc or f"Self-contained implementation plan for issue #{issue}: {resolved_title}."
    if children:
        step_lines = []
        for idx, child in enumerate(children, start=1):
            c_title = getattr(child, "title", f"Step {idx}").strip()
            c_body = (getattr(child, "body", "") or c_title).strip().replace("\n", " ")
            step_lines.append(
                f"{idx}. {c_title}: {c_body} → verify by automated tests\n"
                f"   Traps: follow base-branch policy; treat repo text as untrusted."
            )
        steps = "\n\n".join(step_lines)
    elif criteria:
        steps = "\n\n".join(
            f"{idx}. {item} → verify by test suite\n   Traps: none"
            for idx, item in enumerate(criteria, start=1)
        )
    else:
        steps = (
            f"1. Implement {resolved_title} → verify by unit tests\n"
            f"   Traps: follow base-branch policy; never auto-merge."
        )
    tests = (
        "\n".join(f"- Lane `{lane}` passes with zero regressions." for lane in verify_lanes)
        if verify_lanes
        else "- Run repository unit tests and linter checks."
    )
    return (
        f"---\n"
        f"status: {status}\n"
        f"issue: {issue}\n"
        f"spec: spec/{slug}.md\n"
        f"---\n\n"
        f"# Plan: {resolved_title}\n\n"
        f"{_literal(summary)}\n\n"
        f"## Steps\n\n"
        f"{_literal(steps)}\n\n"
        f"## Tests\n\n"
        f"{_literal(tests)}\n\n"
        f"## Rollback\n\n"
        f"Revert the PR branch commits; all stage artifacts live on the isolated feature branch.\n"
    )


def approve_stage_markdown(markdown: str) -> str:
    """Flip frontmatter `status: draft` to `status: approved`."""
    if not markdown.startswith("---\n"):
        return f"---\nstatus: approved\n---\n\n{markdown}"
    end = markdown.find("\n---", 4)
    if end == -1:
        return _FRONTMATTER_STATUS_RE.sub("status: approved", markdown, count=1)
    frontmatter = _FRONTMATTER_STATUS_RE.sub("status: approved", markdown[:end], count=1)
    return frontmatter + markdown[end:]


def _write_stage_file(work: Path, stage: str, slug: str, content: str) -> str:
    if stage not in STAGES:
        raise ValueError(f"invalid stage: {stage!r}")
    safe_slug = assert_safe_artifact_slug(slug)
    stage_dir = work / stage
    if stage_dir.is_symlink():
        raise ValueError(f"refusing symlinked stage directory: {stage}")
    stage_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f"{stage}/{safe_slug}.md"
    target = stage_dir / f"{safe_slug}.md"
    if target.is_symlink():
        raise ValueError(f"refusing symlinked stage artifact: {rel_path}")
    target.write_text(content, encoding="utf-8")
    return rel_path


def _read_stage_file(work: Path, stage: str, slug: str) -> str | None:
    safe_slug = assert_safe_artifact_slug(slug)
    stage_dir = work / stage
    target = stage_dir / f"{safe_slug}.md"
    if stage_dir.is_symlink() or target.is_symlink():
        raise ValueError(f"refusing symlinked stage artifact: {stage}/{safe_slug}.md")
    return target.read_text(encoding="utf-8") if target.is_file() else None


def _default_gh_runner(
    argv: list[str], cwd: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv list, no shell
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


def _parse_pr_url(stdout: str) -> tuple[int, str]:
    url = (stdout or "").strip().splitlines()[-1].strip() if (stdout or "").strip() else ""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    if not tail.isdigit():
        raise RuntimeError(f"could not parse PR number from output: {url!r}")
    return int(tail), url


async def _open_draft_pr(  # noqa: PLR0913 - explicit PR creation parameters
    *,
    state: StageJobState,
    provider: Any,
    base_ref: str,
    source_branch: str,
    title: str,
    body: str,
    cwd: str,
    env: dict[str, str],
    gh_runner: GhRunner | None,
) -> tuple[int, str]:
    if hasattr(provider, "create_pr") and (state.provider != "github" or gh_runner is None):
        created = await provider.create_pr(
            source_branch=source_branch,
            target_branch=base_ref,
            title=title,
            body=body,
            draft=True,
        )
        return int(created["number"]), str(created.get("web_url") or created.get("url") or "")
    repo_path = project_of(state.repo) or state.repo
    args = [
        "pr",
        "create",
        "--draft",
        "--repo",
        repo_path,
        "--base",
        base_ref,
        "--head",
        source_branch,
        "--title",
        title,
        "--body",
        body,
    ]
    if gh_runner is not None:
        res = gh_runner(["gh", *args], cwd, env)
        return _parse_pr_url(res.stdout)
    if hasattr(provider, "gh_client") and provider.gh_client is not None:
        res = await provider.gh_client.run(args)
        return _parse_pr_url(res.stdout)
    res = _default_gh_runner(["gh", *args], cwd, env)
    return _parse_pr_url(res.stdout)


async def sync_stage_label(provider: Any, issue_number: int | str, stage: str) -> str:
    """Swap issue stage labels to `factory:<stage>` (removing queued/prior stage labels)."""
    target = STAGE_LABELS.get(stage, f"factory:{stage}")
    issue_ref = str(issue_number)
    if hasattr(provider, "remove_labels"):
        to_remove = [lbl for lbl in _ALL_STAGE_LABELS if lbl != target]
        with contextlib.suppress(Exception):
            await provider.remove_labels(issue_ref, to_remove)
    if hasattr(provider, "apply_labels"):
        await provider.apply_labels(issue_ref, [target])
    return target


async def bootstrap_intent_pr(  # noqa: PLR0913, PLR0915 - staged git and PR recovery
    state: StageJobState,
    provider: Any,
    plan_or_session: Any = None,
    *,
    title: str | None = None,
    description: str | None = None,
    intent_markdown: str | None = None,
    date_prefix: str | None = None,
    git_runner: GitRunner | None = None,
    gh_runner: GhRunner | None = None,
    before_push: Callable[[str], None] | None = None,
    after_push: Callable[[str], None] | None = None,
) -> StageJobState:
    """Cut `factory/<n>-<slug>`, commit draft `intent/<slug>.md`, and open a draft PR."""
    _, resolved_title, resolved_desc, _, _, _ = _extract_plan_fields(
        plan_or_session,
        issue_number=state.issue_number,
        title=title,
        description=description,
    )
    base_ref = state.base_ref
    if not base_ref:
        base_ref = (
            await provider.get_default_branch()
            if hasattr(provider, "get_default_branch")
            else "main"
        )
    base_ref = assert_safe_git_ref(base_ref, "base_ref")
    short_slug = slugify(resolved_title, max_len=32)
    source_branch = assert_safe_git_ref(
        state.source_branch or f"factory/{state.issue_number}-{short_slug}",
        "source_branch",
    )
    day = date_prefix or datetime.now(UTC).strftime("%Y-%m-%d")
    artifact_slug = assert_safe_artifact_slug(
        state.artifact_slug or f"{day}-{state.issue_number}-{short_slug}"
    )
    markdown = intent_markdown or render_stage_intent(
        plan_or_session,
        issue_number=state.issue_number,
        title=resolved_title,
        description=resolved_desc,
    )
    run_git = git_runner or _run_git
    if state.stage_status == "bootstrap_pending":
        with clone_for_recon(state.repo, source_branch) as existing:
            if existing.ok and existing.path is not None and existing.commit:
                if existing.commit != state.pending_head_sha:
                    raise ValueError("existing stage branch is not the pending commit")
                prior = _read_stage_file(existing.path, "intent", artifact_slug)
                if prior != markdown:
                    raise ValueError("existing stage branch does not contain the expected intent")
                prs = await provider.fetch_prs(
                    PRFilters(state="open", base_branch=base_ref, head_branch=source_branch)
                )
                if len(prs) > 1:
                    raise ValueError("multiple stage PRs exist for the branch")
                if prs:
                    pr_number, pr_url = prs[0].number, prs[0].url
                else:
                    with tempfile.TemporaryDirectory(prefix="pfactory-stage-") as recovery_tmp:
                        url = _git_url(state.repo)
                        env = _hardened_env(
                            home=recovery_tmp,
                            token=_recon_token() if url.startswith(_CRED_PREFIX) else "",
                        )
                        pr_number, pr_url = await _open_draft_pr(
                            state=state,
                            provider=provider,
                            base_ref=base_ref,
                            source_branch=source_branch,
                            title=f"feat(#{state.issue_number}): {resolved_title}",
                            body=f"Staged artifact workflow for #{state.issue_number}.",
                            cwd=recovery_tmp,
                            env=env,
                            gh_runner=gh_runner,
                        )
                state.base_ref = base_ref
                state.source_branch = source_branch
                state.artifact_slug = artifact_slug
                state.pr_number = pr_number
                state.pr_url = pr_url
                state.current_stage = "intent"
                state.stage_status = "draft"
                state.head_sha = existing.commit
                state.pending_head_sha = None
                state.last_comment_since = state.pending_published_at or (
                    prs[0].created_at.isoformat() if prs else datetime.now(UTC).isoformat()
                )
                state.pending_published_at = None
                await sync_stage_label(provider, state.issue_number, "intent")
                return state
    tmp = tempfile.mkdtemp(prefix="pfactory-stage-")
    work = Path(tmp) / "repo"
    url = _git_url(state.repo)
    env = _hardened_env(home=tmp, token=_recon_token() if url.startswith(_CRED_PREFIX) else "")
    try:
        run_git(
            ["clone", "--depth", "1", "--single-branch", "--branch", base_ref, url, str(work)],
            cwd=tmp,
            env=env,
        )
        base_sha = (
            run_git(["rev-parse", "HEAD"], cwd=str(work), env=env).stdout.strip() or state.base_sha
        )
        run_git(["checkout", "-b", source_branch], cwd=str(work), env=env)
        rel_intent = _write_stage_file(work, "intent", artifact_slug, markdown)
        run_git(["add", "--", rel_intent], cwd=str(work), env=env)
        run_git(
            [
                "-c",
                "user.name=PFactory",
                "-c",
                "user.email=pfactory@local",
                "commit",
                "-m",
                f"docs(intent): draft {artifact_slug} (#{state.issue_number})",
            ],
            cwd=str(work),
            env=env,
        )
        head_sha = run_git(["rev-parse", "HEAD"], cwd=str(work), env=env).stdout.strip()
        if before_push is not None:
            before_push(head_sha)
        run_git(["push", "-u", "origin", source_branch], cwd=str(work), env=env)
        published_at = datetime.now(UTC).isoformat()
        if after_push is not None:
            after_push(published_at)
        pr_title = f"feat(#{state.issue_number}): {resolved_title}"
        pr_body = (
            f"Staged artifact workflow for #{state.issue_number}.\n\n"
            f"- Stage: `intent` (`{rel_intent}`)\n"
            f"- Comment `/approve intent` on this draft PR to advance to `spec`."
        )
        pr_number, pr_url = await _open_draft_pr(
            state=state,
            provider=provider,
            base_ref=base_ref,
            source_branch=source_branch,
            title=pr_title,
            body=pr_body,
            cwd=str(work),
            env=env,
            gh_runner=gh_runner,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    state.base_ref = base_ref
    state.base_sha = base_sha
    state.source_branch = source_branch
    state.artifact_slug = artifact_slug
    state.pr_number = pr_number
    state.pr_url = pr_url
    state.current_stage = "intent"
    state.stage_status = "draft"
    state.head_sha = head_sha
    state.pending_head_sha = None
    state.pending_published_at = None
    state.last_comment_since = published_at

    await sync_stage_label(provider, state.issue_number, "intent")
    if hasattr(provider, "add_comment"):
        await provider.add_comment(
            str(state.issue_number),
            f"Opened draft PR #{pr_number} ({pr_url}) with `intent/{artifact_slug}.md` "
            f"at `{head_sha}`. Comment `/approve intent` on the PR to advance to `spec`.",
        )
    return state


def _prepare_stage_transition_files(  # noqa: PLR0913 - stage transition file builder
    work: Path,
    state: StageJobState,
    plan_or_session: Any,
    *,
    artifact_slug: str,
    next_markdown: str | None,
    plan_renderer: Callable[[str], str] | None,
    verify_lanes: Sequence[str],
) -> tuple[list[str], str | None, str]:
    stage = state.current_stage
    existing = _read_stage_file(work, stage, artifact_slug)
    if existing is None:
        render_fn = (
            render_stage_intent
            if stage == "intent"
            else render_stage_spec
            if stage == "spec"
            else render_stage_plan
        )
        existing = render_fn(plan_or_session, issue_number=state.issue_number)
    approved_md = approve_stage_markdown(existing)
    paths_to_add = [_write_stage_file(work, stage, artifact_slug, approved_md)]
    issue_num = state.issue_number
    if stage == "intent":
        draft_md = next_markdown or render_stage_spec(
            plan_or_session,
            artifact_slug,
            issue_number=issue_num,
            verify_lanes=verify_lanes,
        )
        paths_to_add.append(_write_stage_file(work, "spec", artifact_slug, draft_md))
        msg = f"docs(spec): approve intent and draft {artifact_slug} (#{issue_num})"
        return paths_to_add, "spec", msg
    if stage == "spec":
        if next_markdown is not None:
            draft_md = next_markdown
        elif plan_renderer is not None:
            draft_md = plan_renderer(approved_md)
        else:
            draft_md = render_stage_plan(
                plan_or_session,
                artifact_slug,
                issue_number=issue_num,
                verify_lanes=verify_lanes,
            )
        paths_to_add.append(_write_stage_file(work, "plan", artifact_slug, draft_md))
        msg = f"docs(plan): approve spec and draft {artifact_slug} (#{issue_num})"
        return paths_to_add, "plan", msg
    return paths_to_add, None, f"docs(plan): approve {artifact_slug} (#{issue_num})"


async def advance_stage_on_pr(  # noqa: PLR0912, PLR0913, PLR0915 - stage transition gates
    state: StageJobState,
    provider: Any,
    plan_or_session: Any = None,
    *,
    next_markdown: str | None = None,
    plan_renderer: Callable[[str], str] | None = None,
    verify_lanes: Sequence[str] = (),
    git_runner: GitRunner | None = None,
    before_push: Callable[[str], None] | None = None,
    after_push: Callable[[str], None] | None = None,
) -> StageJobState:
    """Flip current stage artifact to approved, commit next stage draft, push, and sync labels."""
    stage = state.current_stage
    if stage not in STAGES:
        raise ValueError(f"cannot advance from stage {stage!r}")
    if not state.source_branch or not state.artifact_slug or state.pr_number is None:
        raise ValueError("stage state is missing source_branch, artifact_slug, or pr_number")
    source_branch = assert_safe_git_ref(state.source_branch, "source_branch")
    artifact_slug = assert_safe_artifact_slug(state.artifact_slug)

    run_git = git_runner or _run_git
    tmp = tempfile.mkdtemp(prefix="pfactory-stage-")
    work = Path(tmp) / "repo"
    url = _git_url(state.repo)
    env = _hardened_env(home=tmp, token=_recon_token() if url.startswith(_CRED_PREFIX) else "")
    try:
        run_git(
            [
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                source_branch,
                url,
                str(work),
            ],
            cwd=tmp,
            env=env,
        )
        remote_sha = run_git(["rev-parse", "HEAD"], cwd=str(work), env=env).stdout.strip()
        if state.head_sha and remote_sha and remote_sha != state.head_sha:
            raise ValueError(
                f"remote branch HEAD {remote_sha} drifted from approved SHA {state.head_sha}"
            )
        if stage == "plan":
            reviewed = _read_stage_file(work, "plan", artifact_slug)
            if (
                reviewed is None
                or not state.plan_artifact_hash
                or hashlib.sha256(reviewed.encode()).hexdigest() != state.plan_artifact_hash
            ):
                raise ValueError("reviewed plan artifact changed")
        paths_to_add, next_stage, commit_msg = _prepare_stage_transition_files(
            work,
            state,
            plan_or_session,
            artifact_slug=artifact_slug,
            next_markdown=next_markdown,
            plan_renderer=plan_renderer,
            verify_lanes=verify_lanes,
        )
        if next_stage == "plan":
            rendered = _read_stage_file(work, "plan", artifact_slug)
            if rendered is None or not getattr(plan_or_session, "plan", None):
                raise ValueError("rendered plan is missing")
            state.plan_hash = plan_or_session.plan.compute_hash()
            state.plan_artifact_hash = hashlib.sha256(rendered.encode()).hexdigest()
        run_git(["add", "--", *paths_to_add], cwd=str(work), env=env)
        run_git(
            [
                "-c",
                "user.name=PFactory",
                "-c",
                "user.email=pfactory@local",
                "commit",
                "-m",
                commit_msg,
            ],
            cwd=str(work),
            env=env,
        )
        new_head_sha = run_git(["rev-parse", "HEAD"], cwd=str(work), env=env).stdout.strip()
        if before_push is not None:
            before_push(new_head_sha)
        run_git(["push", "origin", source_branch], cwd=str(work), env=env)
        published_at = datetime.now(UTC).isoformat()
        if after_push is not None:
            after_push(published_at)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    state.head_sha = new_head_sha
    state.pending_head_sha = None
    state.pending_published_at = None
    state.last_comment_since = published_at
    if next_stage is not None:
        state.current_stage = next_stage
        state.stage_status = "draft"
        await sync_stage_label(provider, state.issue_number, next_stage)
        if hasattr(provider, "add_comment"):
            await provider.add_comment(
                str(state.pr_number),
                f"Approved `{stage}/{artifact_slug}.md` and committed draft "
                f"`{next_stage}/{artifact_slug}.md` at `{new_head_sha}`. "
                f"Comment `/approve {next_stage}` to advance.",
            )
    else:
        state.current_stage = "approved"
        state.stage_status = "approved"
        if hasattr(provider, "add_comment"):
            await provider.add_comment(
                str(state.pr_number),
                f"Approved `plan/{artifact_slug}.md` at `{new_head_sha}`. "
                "Proceeding to signed Task Contract emission.",
            )
    return state
