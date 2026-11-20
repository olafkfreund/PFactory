"""Base-branch policy and single-use PR approvals for issue stages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from plan.recon.clone import clone_for_recon

STAGES = ("intent", "spec", "plan")
_APPROVE = re.compile(r"^\s*/approve\s+(intent|spec|plan)(?:\s|$)")
_CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS")
_GITLAB_WRITE_ACCESS = 30


@dataclass(frozen=True)
class BoundApproval:
    stage: str
    pr_number: int
    head_sha: str
    comment_id: str
    provider_user_id: int
    username: str
    policy_source: str
    approved_at: str


@dataclass
class StageJobState:
    tenant_id: str
    provider: str
    repo: str
    issue_number: int
    project_id: str | None = None
    base_ref: str | None = None
    base_sha: str | None = None
    source_branch: str | None = None
    artifact_slug: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    current_stage: str = "intent"
    stage_status: str = "draft"
    head_sha: str | None = None
    plan_hash: str | None = None
    plan_artifact_hash: str | None = None
    approved_head_sha: str | None = None
    pending_head_sha: str | None = None
    pending_published_at: str | None = None
    last_comment_since: str | None = None
    approvals: list[BoundApproval] = field(default_factory=list)
    consumed_comment_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StagePolicy:
    base_sha: str
    stages: tuple[str, ...]
    approvers: dict[str, tuple[str, ...]]
    codeowners: tuple[tuple[str, tuple[str, ...]], ...]
    base_branch: str | None = None
    verify_lanes: tuple[str, ...] = ()

    def owners_for(self, stage: str, artifact_slug: str) -> tuple[set[str], str]:
        path = f"{stage}/{artifact_slug}.md"
        owners = set(self.approvers.get(stage, ()))
        source = ".factory.yml" if stage in self.approvers else "repository permission"
        for pattern, rule_owners in self.codeowners:
            if _matches(pattern, path):
                owners = set(rule_owners)
                source = f"CODEOWNERS {pattern}"
        return owners, source


def _matches(pattern: str, path: str) -> bool:
    """Match the CODEOWNERS path subset, with * scoped to one path component."""
    anchored = pattern.startswith("/")
    pattern = pattern.lstrip("/")
    directory = pattern.endswith("/")
    pattern = pattern.rstrip("/")
    if not pattern or pattern.startswith("!"):
        return False
    regex = ""
    i = 0
    while i < len(pattern):
        if pattern[i : i + 2] == "**":
            if pattern[i + 2 : i + 3] == "/":
                regex += "(?:.*/)?"
                i += 3
            else:
                regex += ".*"
                i += 2
        elif pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            regex += "[^/]"
            i += 1
        else:
            regex += re.escape(pattern[i])
            i += 1
    prefix = "^" if anchored or "/" in pattern else r"(?:^|/)"
    suffix = r"(?:/.*)?$" if directory or not any(char in pattern for char in "*?") else "$"
    return re.search(prefix + regex + suffix, path) is not None


def _read_codeowners(root: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    for name in _CODEOWNERS_PATHS:
        file = root / name
        if file.is_symlink() or file.parent.is_symlink():
            raise ValueError("CODEOWNERS must be a regular file")
        if file.is_file():
            rules = []
            for line in file.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if parts and not parts[0].startswith(("#", "!")):
                    rules.append(
                        (parts[0], tuple(part for part in parts[1:] if part.startswith("@")))
                    )
            return tuple(rules)
    return ()


def load_stage_policy(repo: str, base_ref: str | None = None, tier: str = "hard") -> StagePolicy:
    """Read policy only from the trusted base checkout; malformed policy fails closed."""
    with clone_for_recon(repo, base_ref) as clone:
        if not clone.ok or clone.path is None or clone.commit is None:
            raise ValueError("base branch policy could not be read")
        file = clone.path / ".factory.yml"
        if file.is_symlink():
            raise ValueError("base branch policy must be a regular file")
        config = yaml.safe_load(file.read_text(encoding="utf-8")) if file.is_file() else {}
        if not isinstance(config, dict):
            raise ValueError("invalid .factory.yml policy")
        tiers = config.get("tiers", {})
        if not isinstance(tiers, dict):
            raise ValueError("invalid tier policy")
        tier_config = tiers.get(tier, {})
        if not isinstance(tier_config, dict):
            raise ValueError("invalid tier policy")
        stages = tier_config.get("stages", list(STAGES))
        raw_approvers = config.get("approvers", {})
        if isinstance(raw_approvers, list):
            raw_approvers = dict.fromkeys(STAGES, raw_approvers)
        if not isinstance(raw_approvers, dict) or not isinstance(stages, list):
            raise ValueError("invalid stage approvers or stages")
        approvers = {}
        for stage, names in raw_approvers.items():
            if (
                stage not in STAGES
                or not isinstance(names, list)
                or not all(isinstance(name, str) and name.startswith("@") for name in names)
            ):
                raise ValueError("invalid stage approvers")
            approvers[stage] = tuple(names)
        if not all(stage in STAGES for stage in stages):
            raise ValueError("invalid stages")
        verify = config.get("verify", {})
        if not isinstance(verify, dict):
            raise ValueError("invalid verify policy")
        lanes = verify.get("lanes", [])
        if not isinstance(lanes, list) or not all(isinstance(lane, str) for lane in lanes):
            raise ValueError("invalid verify lanes")
        return StagePolicy(
            base_sha=clone.commit,
            stages=tuple(stages),
            approvers=approvers,
            codeowners=_read_codeowners(clone.path),
            base_branch=config.get("base_branch"),
            verify_lanes=tuple(lanes),
        )


def parse_approval(body: str) -> str | None:
    match = _APPROVE.match(body)
    return match.group(1) if match else None


def provider_user_id(comment: Any, provider_name: str) -> int | None:
    key = "user" if provider_name == "github" else "author" if provider_name == "gitlab" else None
    raw = comment.raw_data.get(key, {}) if key else {}
    value = raw.get("id") if isinstance(raw, dict) else None
    return value if type(value) is int and value > 0 else None


async def verify_bound_approval(  # noqa: PLR0913, PLR0911, PLR0912 - explicit security gates
    state: StageJobState,
    policy: StagePolicy,
    comment: Any,
    provider: Any,
    live_head_sha: str,
    stage_commit_at: datetime,
) -> BoundApproval | None:
    """Consume a stage command once; reject stale SHA, identity, and policy failures."""
    stage = parse_approval(comment.body)
    if stage is None or str(comment.id) in state.consumed_comment_ids:
        return None
    state.consumed_comment_ids.append(str(comment.id))
    raw_updated_at = comment.raw_data.get("updated_at")
    if state.provider in {"github", "gitlab"} and raw_updated_at is None:
        return None
    updated_at = raw_updated_at or getattr(comment, "updated_at", None)
    if updated_at is not None:
        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            except ValueError:
                return None
        if updated_at != comment.created_at:
            return None
    if (
        stage != state.current_stage
        or state.stage_status != "draft"
        or state.pr_number is None
        or not state.head_sha
        or live_head_sha != state.head_sha
        or comment.created_at < stage_commit_at
        or not state.artifact_slug
    ):
        return None
    user_id = provider_user_id(comment, state.provider)
    if user_id is None or not comment.author:
        return None
    try:
        if state.provider == "github":
            user = await provider.api_get(f"/users/{comment.author}")
        elif state.provider == "gitlab":
            project = provider._project_id
            user = await provider.api_get(f"/api/v4/projects/{project}/members/all/{user_id}")
        else:
            return None
    except Exception:  # noqa: BLE001 - a failed identity lookup rejects the consumed comment
        return None
    if not isinstance(user, dict) or user.get("id") != user_id:
        return None
    if str(user.get("login", user.get("username", ""))).lower() != comment.author.lower():
        return None
    owners, source = policy.owners_for(stage, state.artifact_slug)
    if source != "repository permission":
        allowed = f"@{comment.author}".lower() in {owner.lower() for owner in owners}
    elif state.provider == "gitlab":
        allowed = user.get("access_level", 0) >= _GITLAB_WRITE_ACCESS
    else:
        try:
            allowed = await provider.check_permissions(comment.author) in {"write", "admin"}
        except Exception:  # noqa: BLE001 - permission API failure consumes the comment
            return None
    if not allowed:
        return None
    approval = BoundApproval(
        stage=stage,
        pr_number=state.pr_number,
        head_sha=state.head_sha,
        comment_id=str(comment.id),
        provider_user_id=user_id,
        username=comment.author,
        policy_source=source,
        approved_at=comment.created_at.isoformat(),
    )
    state.approvals.append(approval)
    return approval
