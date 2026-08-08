"""Triager agent — Task 8, issue #9 + Task 11, issue #27.

Final agent in the six-agent PFactory pipeline:

    Planner → Gen-Functional → Executor → Evaluator → Triager

Reads ``findings/verdicts.json`` (written by the Evaluator), wraps
each entry as a TriageCandidate, filters out rejects, dedups +
ranks via the commit-2 primitives, renders triage_report.{md,json}
via commit-3's renderer, then invokes the commit-4 git_writer +
pr_comment helpers (both DRY-RUN by default for safety — production
opts in via env flags).

Per CLAUDE.md: "NO automatic pushes to GitHub - user controls when
to push". Real-run defaults are off; the operator flips them per
deployment.

Task 8 commits (all landed):

  ✓ commit 1 — Auto-fire scaffold + stub
  ✓ commit 2 — Dedup + rank primitives
  ✓ commit 3 — Triage report rendering (golden-file snapshot)
  ✓ commit 4 — git_writer + pr_comment helpers (dry-run first)
  ✓ commit 5 — Real run_triager wires everything
  ✓ commit 6 — Integration test + close #9

  (Sub-task 8.4 — trim AIFactory's runners/github/ to a minimal
  pr_comment.py — is deferred to a follow-up commit; the web-server
  still consumes pieces of that tree and needs careful surgery.)

Task 11 commits (v0.2 catalog-aware Triager):

  ✓ commit 1 — Catalog read at Triager start + CandidateDecision dataclass
  ✓ commit 2 — lookup_by_ac integration + intent derivation
  ✓ commit 3 — UPDATE vs CREATE branching + framework path derivation
  ✓ commit 4 — SKIP for operator_locked + report rendering with intent
  ✓ commit 5 — Catalog mutation + 18+ test cases + close #27
"""

from __future__ import annotations

import asyncio
import json
import logging as _logging
import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agents.agent_infra import now_iso, read_status, truthy, write_status_patch

if TYPE_CHECKING:
    # tests_catalog is imported LAZILY at the three call sites below, but
    # `_mutate_catalog` and friends annotate `TestsCatalog` at module level.
    # `from __future__ import annotations` makes that a string at runtime, so
    # nothing broke -- but no checker could resolve the name either, so the
    # annotation asserted nothing and mypy reported [name-defined]. This block
    # is type-time only: the runtime import stays lazy.
    from tests_catalog import TestsCatalog

_triage_log = _logging.getLogger(__name__)


# ─── Task 11: per-candidate catalog decision ───────────────────────────


@dataclass(frozen=True)
class CandidateDecision:
    """Catalog-aware decision for one TriageCandidate (Task 11 / #27).

    Produced by ``_decide_catalog_intent`` after consulting the tests
    catalog via ``lookup_by_ac``.

    Attributes:
        intent: One of ``"create"``, ``"update"``, or ``"skip"``.
        update_target_file: Repo-relative path of the existing test file
            when ``intent == "update"``.  ``None`` otherwise.
        skip_reason: Human-readable reason for ``intent == "skip"``,
            e.g. ``"operator_locked"``.  ``None`` otherwise.
        derived_test_file: For ``intent == "create"``, the framework-
            conventional path for the new test file.  May be ``None``
            when the framework is unknown.
    """

    intent: Literal["create", "update", "skip"] = "create"
    update_target_file: str | None = None
    skip_reason: str | None = None
    derived_test_file: str | None = None


# ─── Task 11: catalog IO helpers ───────────────────────────────────────


def _load_catalog_from_spec(spec_dir: Path):
    """Load tests_catalog from ``spec_dir/context/tests_catalog.json``.

    Returns a ``TestsCatalog`` when the file exists and parses, or
    ``None`` when absent (v0.1-style run — every candidate is CREATE
    with no catalog mutation).

    Logs and returns ``None`` on parse failure rather than raising, to
    keep the Triager's happy path unaffected by a corrupt catalog file.
    """
    catalog_path = spec_dir / "context" / "tests_catalog.json"
    if not catalog_path.exists():
        return None
    try:
        from tests_catalog import TestsCatalog

        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        return TestsCatalog.from_dict(raw)
    except Exception as exc:  # noqa: BLE001
        _triage_log.warning(
            "triager: could not parse tests_catalog.json — falling back to v0.1 (all CREATE): %s",
            exc,
        )
        return None


# ─── Task 11: lookup_by_ac + intent decision (commit 2) ────────────────


def _extract_candidate_ac(candidate) -> str:
    """Extract the AC string from a TriageCandidate for catalog lookup.

    The rationale field of the verdict dict carries the AC reference
    (e.g. ``"AC#1: login sets 24h expiry"``).  Returns ``""`` when
    the rationale is absent or empty — the caller treats that as a
    no-match, forcing CREATE intent.
    """
    rationale = candidate.verdict.get("rationale") or ""
    if not rationale:
        # Fallback: check the reasons list for an "AC#" prefix
        reasons = candidate.verdict.get("reasons") or []
        for r in reasons:
            if isinstance(r, str) and r.strip().startswith("AC"):
                return r.strip()
    return str(rationale).strip()


def _decide_catalog_intent(candidate, catalog) -> CandidateDecision:
    """Decide CREATE / UPDATE / SKIP for *candidate* against *catalog*.

    Args:
        candidate: A ``TriageCandidate`` (accepted or flagged).
        catalog: A ``TestsCatalog`` or ``None``.  When ``None`` every
            candidate is CREATE (v0.1 backward-compat path).

    Returns:
        A ``CandidateDecision`` with the appropriate intent fields set.

    Policy (from design doc, §"Update-vs-create policy"):
    - ``catalog is None``          → CREATE (no catalog at all)
    - ``len(matches) == 0``        → CREATE (no existing test)
    - ``matches[0].operator_locked`` → SKIP (operator pinned)
    - ``len(matches) == 1``        → UPDATE (clear single match)
    - ``len(matches) > 1``         → UPDATE on most-recent + warn
    """
    if catalog is None:
        return CandidateDecision(intent="create")

    from tests_catalog import lookup_by_ac

    candidate_ac = _extract_candidate_ac(candidate)
    matches = lookup_by_ac(catalog, candidate_ac) if candidate_ac else []

    if not matches:
        return CandidateDecision(intent="create")

    # Operator-locked check comes BEFORE the single/multi split so that
    # a locked entry always wins, even when it's the only match.
    if matches[0].operator_locked:
        return CandidateDecision(
            intent="skip",
            skip_reason="operator_locked",
        )

    if len(matches) > 1:
        _triage_log.warning(
            "triager: catalog ambiguity — %d entries match AC %r for "
            "candidate %r; picking most-recent entry as UPDATE target",
            len(matches),
            candidate_ac,
            candidate.test_id,
        )
        best = max(matches, key=lambda e: e.generated_at)
    else:
        best = matches[0]

    return CandidateDecision(
        intent="update",
        update_target_file=best.test_file,
    )


# ─── Task 11: framework-conventional path derivation (commit 3) ────────

# Fallback extension map when the framework registry cannot be loaded.
_FRAMEWORK_EXTENSION_FALLBACK: dict[str, str] = {
    "playwright": ".spec.ts",
    "jest": ".test.ts",
    "pytest": ".py",
}


def _derive_create_path(test_id: str, framework: str) -> str:
    """Derive the conventional test-file path for a new test.

    Consults the framework registry's ``test_path_conventions`` to pick
    the first pattern, replaces the glob wildcards with the test_id,
    and returns a repo-relative path string.

    Falls back to a sensible default when the framework is unknown or
    the registry is unavailable (e.g. in test environments without the
    ``frameworks/`` directory).

    Args:
        test_id: The test identifier (e.g. ``"ac1-login-flow"``).
        framework: Framework name string (e.g. ``"playwright"``).

    Returns:
        A repo-relative path string like ``"tests/e2e/ac1-login-flow.spec.ts"``.
    """
    try:
        from framework_registry.loader import get_descriptor

        desc = get_descriptor(framework)
        if desc.test_path_conventions:
            # Take the first convention pattern.
            pattern = desc.test_path_conventions[0]
            # Strip glob wildcards to get the directory prefix.
            # e.g. "tests/e2e/**/*.spec.ts" → dir="tests/e2e", ext=".spec.ts"
            # e.g. "tests/**/test_*.py"     → dir="tests",     ext=".py"
            # e.g. "**/*.test.ts"           → dir="",           ext=".test.ts"
            import posixpath

            parts = pattern.replace("\\", "/").split("/")
            dir_parts = [p for p in parts[:-1] if p and p != "**"]
            filename_pattern = parts[-1]
            # Extract extension from the filename glob
            if "." in filename_pattern:
                ext = filename_pattern[filename_pattern.rindex(".") :]
            else:
                ext = _FRAMEWORK_EXTENSION_FALLBACK.get(framework, ".py")
            dir_prefix = "/".join(dir_parts) if dir_parts else "tests"
            return posixpath.join(dir_prefix, f"{test_id}{ext}")
    except Exception:  # noqa: BLE001
        pass

    # Fallback: derive from known extensions
    ext = _FRAMEWORK_EXTENSION_FALLBACK.get(framework, ".py")
    return f"tests/{test_id}{ext}"


# ─── Task 11: catalog mutation (commit 5) ──────────────────────────────


def _mutate_catalog(
    catalog,
    candidates,
    decisions: dict[str, CandidateDecision],
    generated_by_task: str,
    now_ts: str,
) -> TestsCatalog | None:
    """Apply UPDATE / CREATE decisions to *catalog* and return a new catalog.

    Per the design doc policy:
    - UPDATE: bump generation_version; refresh generated_at + last_verdict
    - CREATE: append a new CatalogEntry
    - SKIP (operator_locked): leave entry untouched
    - REJECT candidates: not in decisions dict → no catalog mutation

    Args:
        catalog: The current ``TestsCatalog`` (may be ``None`` for v0.1
            runs — returns ``None`` in that case so the caller skips
            save_catalog).
        candidates: All TriageCandidates (accept + flag only — rejects
            are already excluded from decisions).
        decisions: Mapping test_id → CandidateDecision from the intent
            derivation step.
        generated_by_task: Spec-ID to record in new catalog entries.
        now_ts: ISO-8601 UTC timestamp string to use for generated_at.

    Returns:
        An updated ``TestsCatalog``, or ``None`` when *catalog* is
        ``None`` (v0.1 flow — no catalog file at all).
    """
    if catalog is None:
        return None

    from tests_catalog import CatalogEntry, TestsCatalog

    # Build a mutable list so we can update in place.
    entries: list = list(catalog.tests)

    # Index existing entries by test_file for O(1) UPDATE lookup.
    file_index: dict[str, int] = {e.test_file: i for i, e in enumerate(entries)}

    for c in candidates:
        decision = decisions.get(c.test_id)
        if decision is None:
            continue  # reject or unprocessed

        if decision.intent == "skip":
            # operator_locked — leave the catalog entry untouched
            continue

        verdict_label = c.verdict_label

        if decision.intent == "update" and decision.update_target_file:
            idx = file_index.get(decision.update_target_file)
            if idx is not None:
                old = entries[idx]
                # Frozen dataclass — replace with new instance
                entries[idx] = CatalogEntry(
                    test_id=old.test_id,
                    test_file=old.test_file,
                    framework=old.framework,
                    lane=old.lane,
                    language=old.language,
                    covers_acs=old.covers_acs,
                    generated_at=now_ts,
                    generated_by_task=old.generated_by_task,
                    last_verdict=verdict_label,
                    browsers_tested=old.browsers_tested,
                    target_ref=old.target_ref,
                    operator_locked=old.operator_locked,
                    generation_version=old.generation_version + 1,
                )

        elif decision.intent == "create":
            test_file = decision.derived_test_file or c.test_file
            # Avoid duplicate entries if the same test appears twice
            if test_file not in file_index:
                verdict_dict = c.verdict
                framework = verdict_dict.get("framework") or "pytest"
                language = verdict_dict.get("language") or "python"
                lane_str = verdict_dict.get("lane") or "unit"
                new_entry = CatalogEntry(
                    test_id=c.test_id,
                    test_file=test_file,
                    framework=framework,
                    lane=lane_str,
                    language=language,
                    covers_acs=tuple(verdict_dict.get("covers_acs") or []),
                    generated_at=now_ts,
                    generated_by_task=generated_by_task,
                    last_verdict=verdict_label,
                    generation_version=1,
                )
                entries.append(new_entry)
                file_index[test_file] = len(entries) - 1

    return TestsCatalog(
        version=catalog.version,
        updated_at=now_ts,
        tests=tuple(entries),
    )


# ─── Workspace helpers ─────────────────────────────────────────────────


# now_iso / read_status are shared via agents.agent_infra (#195); keep the
# historical private aliases.
_now_iso = now_iso
_read_status = read_status


# Terminal statuses the Triager can land on. A completion callback fires once
# the task reaches any of these (see _notify_completion).
_TERMINAL_STATUSES = frozenset({"triaged", "triaged_empty", "triager_failed"})


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    # Shared read/merge/write + best-effort #95 stage event (stage="triager").
    write_status_patch(spec_dir, "triager", **fields)
    # Fire the completion callback exactly once, when the task goes terminal.
    if fields.get("status") in _TERMINAL_STATUSES:
        # Re-read the just-written status (identical content to the in-memory
        # merged dict the previous inline version passed through).
        status = _read_status(spec_dir)
        _notify_completion(spec_dir, status)
        # Best-effort PFactory→AIFactory correction hand-back (#185 / epic #182).
        # Prepares findings/handback_request.{md,json} when the run has failures;
        # sends to AIFactory only with PFACTORY_HANDBACK_SEND=1. Never raises.
        from agents.handback.trigger import maybe_handback

        maybe_handback(spec_dir)


# ─── Mode resolution: dry-run vs real ─────────────────────────────────


_truthy = truthy


def _git_writer_dry_run() -> bool:
    """Default ON (dry). Operator sets PFACTORY_TRIAGER_GIT_WRITE=1 to
    actually commit to the AIFactory branch."""
    return not _truthy(os.environ.get("PFACTORY_TRIAGER_GIT_WRITE"))


def _pr_comment_dry_run() -> bool:
    """Default ON (dry). Operator sets PFACTORY_TRIAGER_PR_COMMENT=1 to
    actually post via gh pr comment."""
    return not _truthy(os.environ.get("PFACTORY_TRIAGER_PR_COMMENT"))


def _harvest_enabled() -> bool:
    """Default ON. Promote high-confidence accepts into the reusable template
    library. Writing template files into ``<project>/.pfactory/templates/`` is
    low-risk, so this is on by default; set PFACTORY_TRIAGER_HARVEST=0 to skip."""
    env_val = os.environ.get("PFACTORY_TRIAGER_HARVEST")
    return env_val is None or _truthy(env_val)


def _harvest_global() -> bool:
    """Also write harvested templates to the cross-project global library at
    ``~/.pfactory/templates/``. Opt-in via PFACTORY_TRIAGER_HARVEST_GLOBAL=1."""
    return _truthy(os.environ.get("PFACTORY_TRIAGER_HARVEST_GLOBAL"))


# ─── Completion callback (#85) ──────────────────────────────────────────
# When the Triager reaches a terminal status, optionally notify a watcher so
# the /pfactory-watch round-trip needs no polling. Both channels are OFF by
# default (consistent with the "no automatic side-effects" policy) and are
# strictly best-effort — a missing/failing target must never affect the run.


def _completion_webhook_url() -> str | None:
    """Webhook URL POSTed on completion. Opt-in via PFACTORY_COMPLETION_WEBHOOK."""
    url = (os.environ.get("PFACTORY_COMPLETION_WEBHOOK") or "").strip()
    return url or None


def _completion_sentinel_enabled() -> bool:
    """Write findings/COMPLETED.json on completion. Opt-in via
    PFACTORY_COMPLETION_SENTINEL=1 — a same-host watcher can stat it."""
    return _truthy(os.environ.get("PFACTORY_COMPLETION_SENTINEL"))


def _notify_completion(spec_dir: Path, status: dict) -> None:
    """Best-effort terminal callback. Writes a local sentinel (opt-in) and
    POSTs an env-gated webhook (opt-in). Every failure is swallowed so the
    pipeline can never break on notification."""
    payload = {
        "task_id": status.get("task_id") or spec_dir.name,
        "project_id": status.get("project_id"),
        "status": status.get("status"),
        "phase": status.get("phase"),
        "updated_at": status.get("updated_at"),
    }

    if _completion_sentinel_enabled():
        try:
            findings_dir = spec_dir / "findings"
            findings_dir.mkdir(parents=True, exist_ok=True)
            (findings_dir / "COMPLETED.json").write_text(json.dumps(payload, indent=2))
        except OSError:
            pass

    url = _completion_webhook_url()
    if not url:
        return
    try:
        import urllib.request

        timeout = float(os.environ.get("PFACTORY_COMPLETION_WEBHOOK_TIMEOUT", "5"))
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=timeout).close()  # noqa: S310
    except Exception:
        # Webhook is best-effort; never surface failures into the pipeline.
        pass


# ─── The agent itself ───────────────────────────────────────────────────


async def run_triager(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the PFactory Triager agent.

    Args:
        spec_dir: PFactory workspace spec directory.
        project_dir: AIFactory project root_path (read; used as the
            git_writer's repo_dir when committing).
        mode: 'initial' on first run; 'rerun' for retriggers. Surfaced
            in the report.
        verbose: forwarded for log-level routing in future commits.

    Returns:
        True on clean completion (including dry-run + empty cases);
        False on hard failure.

    Status transitions:
      evaluated   → triaging          (in-flight)
                  → triaged           (report rendered + side-effects done)
                  → triaged_empty     (no verdicts to act on)
                  → triager_failed    (any hard error)
    """
    del verbose
    try:
        _write_status_patch(
            spec_dir,
            status="triaging",
            phase=f"triager_{mode}_started",
        )

        # Lazy imports — keeps test mocking simple + avoids loading
        # heavy modules when the path isn't taken (e.g., evaluator
        # failures land here pre-load).
        from agents.triage_dedup import (
            TriageCandidate,
            dedup_candidates,
            rank_candidates,
        )
        from agents.triage_report import build_report, render_json, render_markdown
        from tools.git_writer import GitWriteRequest, write_tests_to_branch
        from tools.pr_comment import PRCommentRequest, post_pr_comment

        # ── 1. Load verdicts.json ────────────────────────────────
        verdicts_path = spec_dir / "findings" / "verdicts.json"
        if not verdicts_path.exists():
            _write_status_patch(
                spec_dir,
                status="triager_failed",
                phase="triager_no_verdicts",
                triager_error="findings/verdicts.json not found",
            )
            return False

        # Tolerant read via the shared agent-output envelope (#96). The
        # Evaluator already canonicalises verdicts.json, but reading through
        # the same extractor keeps the last consumer robust to a fence or
        # trailing prose rather than hard-failing on it.
        from agents.output_envelope import OutputEnvelopeError, extract_json

        try:
            doc, _salvaged = extract_json(verdicts_path.read_text())
        except OutputEnvelopeError as exc:
            _write_status_patch(
                spec_dir,
                status="triager_failed",
                phase="triager_verdicts_unparseable",
                triager_error=f"verdicts.json invalid: {exc}",
            )
            return False

        verdicts = doc.get("verdicts") or []

        # ── 2. Wrap as TriageCandidates ─────────────────────────
        candidates: list[TriageCandidate] = []
        for v in verdicts:
            tid = v.get("test_id")
            test_file = v.get("test_file")
            if not tid or not test_file:
                # Skip malformed entries — the Evaluator's validator
                # should have caught them, but be defensive
                continue
            test_path = spec_dir / test_file
            source = ""
            if test_path.exists():
                try:
                    source = test_path.read_text(encoding="utf-8")
                except OSError:
                    source = ""
            candidates.append(
                TriageCandidate(
                    test_id=tid,
                    test_file=test_file,
                    verdict=v,
                    source=source,
                )
            )

        # ── 2b. Load catalog + decide intent per accepted/flagged ─
        # (Task 11 / #27 commits 2-3)
        catalog = _load_catalog_from_spec(spec_dir)
        decisions: dict[str, CandidateDecision] = {}
        for c in candidates:
            if c.verdict_label in ("accept", "flag"):
                base_decision = _decide_catalog_intent(c, catalog)
                # For CREATE: enrich with a framework-conventional path
                if base_decision.intent == "create":
                    framework = c.verdict.get("framework") or "pytest"
                    derived = _derive_create_path(c.test_id, framework)
                    decisions[c.test_id] = CandidateDecision(
                        intent="create",
                        derived_test_file=derived,
                    )
                else:
                    decisions[c.test_id] = base_decision
            # rejects: no catalog lookup, no mutation

        # ── 3. Bucket by verdict + dedup the keepers ────────────
        # SKIP candidates (operator_locked) are excluded from dedup/rank
        # so they are not committed or flagged — they only appear in the
        # report's skip section.
        keepers = [
            c
            for c in candidates
            if c.verdict_label in ("accept", "flag")
            and decisions.get(c.test_id, CandidateDecision()).intent != "skip"
        ]
        skipped = [
            c
            for c in candidates
            if c.verdict_label in ("accept", "flag")
            and decisions.get(c.test_id, CandidateDecision()).intent == "skip"
        ]
        rejects = [c for c in candidates if c.verdict_label == "reject"]

        if not candidates:
            # No verdicts at all — empty pass.
            _write_empty_report(spec_dir, mode)
            _write_status_patch(
                spec_dir,
                status="triaged_empty",
                phase="triager_no_candidates",
                committed_count=0,
                rejected_count=0,
                flagged_count=0,
                dedup_collision_count=0,
            )
            return True

        dedup_result = dedup_candidates(keepers)
        ranked_survivors = rank_candidates(dedup_result.kept)
        # Re-bucket the ranked survivors by verdict label
        committed = tuple(c for c in ranked_survivors if c.verdict_label == "accept")
        flagged = tuple(c for c in ranked_survivors if c.verdict_label == "flag")

        # ── 4. Build + render the report ────────────────────────
        report = build_report(
            mode=mode,
            generated_at=_now_iso(),
            committed=committed,
            flagged=flagged,
            rejected=tuple(rejects),
            skipped=tuple(skipped),
            collisions=dedup_result.collisions,
            dedup_input_count=len(keepers),
            decisions=decisions,
            spec_dir=spec_dir,
        )

        findings_dir = spec_dir / "findings"
        findings_dir.mkdir(parents=True, exist_ok=True)
        (findings_dir / "triage_report.json").write_text(render_json(report))
        report_md = render_markdown(report)
        (findings_dir / "triage_report.md").write_text(report_md)

        # ── 5. git_writer side-effect (dry-run by default) ──────
        git_dry = _git_writer_dry_run()
        git_result_summary: dict = {"skipped": True, "reason": "no side-effect path"}
        source_meta = _load_source_meta(spec_dir)
        branch = source_meta.get("branch") or ""
        if committed or flagged:
            files_to_commit = tuple(
                (c.test_file, c.source)
                for c in (*committed, *flagged)
                if c.source  # only commit files we managed to read
            )
            if branch and files_to_commit:
                request = GitWriteRequest(
                    repo_dir=project_dir,
                    branch=branch,
                    files=files_to_commit,
                    commit_msg=(
                        f"pfactory: add {len(committed)} accepted + {len(flagged)} flagged tests"
                    ),
                )
                gw = write_tests_to_branch(request, dry_run=git_dry)
                git_result_summary = {
                    "skipped": False,
                    "dry_run": gw.dry_run,
                    "ok": gw.ok,
                    "committed_paths": list(gw.committed_paths),
                    "commit_sha": gw.commit_sha,
                    "error": gw.error,
                    "argv_log": [list(a) for a in gw.argv_log],
                }
            else:
                git_result_summary = {
                    "skipped": True,
                    "reason": (
                        "no branch in source.json" if not branch else "no readable test sources"
                    ),
                }

        # ── 6. pr_comment side-effect (dry-run by default) ──────
        pr_dry = _pr_comment_dry_run()
        pr_number = int(source_meta.get("pr_number") or 0)
        pr_comment_summary: dict = {"skipped": True, "reason": "no PR number"}
        if pr_number > 0 and report_md:
            # Named apart from the GitWriteRequest bound to `request` earlier in
            # this function: one local name held two unrelated request types, so
            # the second was checked against the first's type and
            # post_pr_comment's argument was reported incompatible. Disjoint
            # branches, so it never misfired -- but the annotation could not say
            # so, which is the whole point of the strict bar.
            pr_request = PRCommentRequest(
                repo_dir=project_dir,
                pr_number=pr_number,
                body=report_md,
                repo_slug=source_meta.get("repo_slug") or None,
            )
            pc = post_pr_comment(pr_request, dry_run=pr_dry)
            pr_comment_summary = {
                "skipped": False,
                "dry_run": pc.dry_run,
                "ok": pc.ok,
                "argv": list(pc.argv),
                "body_bytes": pc.body_bytes,
                "comment_url": pc.comment_url,
                "error": pc.error,
            }
        else:
            # No PR number — write the comment body to disk so the
            # operator can paste it manually.
            (findings_dir / "pr_comment_body.md").write_text(report_md)
            pr_comment_summary = {
                "skipped": True,
                "reason": "no PR number in source.json",
                "body_written_to": str(findings_dir / "pr_comment_body.md"),
            }

        # ── 6b. Catalog mutation (Task 11 / #27 commit 5) ──────────
        # All candidates that went through intent derivation (accept + flag).
        # We pass the full keepers + skipped list (rejects excluded above).
        all_decided = list(keepers) + list(skipped)
        source_meta_for_task = _load_source_meta(spec_dir)
        generated_by_task = (
            source_meta_for_task.get("spec_id") or source_meta_for_task.get("task_id") or "unknown"
        )
        updated_catalog = _mutate_catalog(
            catalog=catalog,
            candidates=all_decided,
            decisions=decisions,
            generated_by_task=generated_by_task,
            now_ts=report.generated_at,
        )
        if updated_catalog is not None:
            # Write back to spec_dir/context/tests_catalog.json — the
            # Triager's workspace copy. The AIFactory repo's authoritative
            # copy lives at <project_dir>/.pfactory/tests-catalog.json and
            # is updated by git_writer (Task 16), not here.
            catalog_out = spec_dir / "context" / "tests_catalog.json"
            catalog_out.parent.mkdir(parents=True, exist_ok=True)
            # The snapshotter pins context/ files read-only (0o444), so make
            # the catalog writable before overwriting. This copy is the
            # Triager's workspace scratch — the authoritative AIFactory-repo
            # catalog is updated by git_writer — so a write failure here must
            # NOT fail the whole Triager; degrade to a warning instead.
            try:
                if catalog_out.exists():
                    catalog_out.chmod(0o644)
                catalog_out.write_text(
                    json.dumps(
                        updated_catalog.to_dict(),
                        indent=2,
                        sort_keys=True,
                        ensure_ascii=False,
                    )
                )
            except OSError as exc:
                _triage_log.warning(
                    "triager: could not update workspace tests_catalog.json (non-fatal): %s",
                    exc,
                )

        # ── 6c. Harvest high-confidence accepts into the reusable
        #        template library (project-local + optional global).
        #        Non-fatal: a harvest failure must never fail the Triager.
        harvested_count = 0
        if _harvest_enabled():
            try:
                from agents.template_harvest import harvest_accepted_tests

                harvested = harvest_accepted_tests(
                    spec_dir,
                    project_dir,
                    keepers,
                    also_global=_harvest_global(),
                )
                harvested_count = len(harvested)
                if harvested_count:
                    _triage_log.info(
                        "triager: harvested %d accepted test(s) into the reusable template library",
                        harvested_count,
                    )
            except Exception as exc:  # noqa: BLE001 — non-fatal side-effect
                _triage_log.warning("triager: template harvest failed (non-fatal): %s", exc)

        # ── 7. Record summaries in status.json ──────────────────
        committed_count = len(committed)
        flagged_count = len(flagged)
        rejected_count = len(rejects)
        collision_count = len(dedup_result.collisions)
        final_status = "triaged" if (committed_count or flagged_count) else "triaged_empty"
        _write_status_patch(
            spec_dir,
            status=final_status,
            phase="triager_complete",
            committed_count=committed_count,
            rejected_count=rejected_count,
            flagged_count=flagged_count,
            dedup_collision_count=collision_count,
            git_writer=git_result_summary,
            pr_comment=pr_comment_summary,
        )
        return True

    except Exception as exc:
        _triage_log.error("triager failed: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="triager_failed",
            phase=f"triager_{mode}_exception",
            triager_error=str(exc)[:500],
        )
        return False


def _load_source_meta(spec_dir: Path) -> dict:
    """Load context/source.json. Returns {} on absence or parse failure."""
    p = spec_dir / "context" / "source.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_empty_report(spec_dir: Path, mode: str) -> None:
    """Write empty placeholder reports for the no-candidates path."""
    from agents.triage_report import build_report, render_json, render_markdown

    empty_report = build_report(
        mode=mode,
        generated_at=_now_iso(),
        committed=[],
        flagged=[],
        rejected=[],
        collisions=[],
        dedup_input_count=0,
    )
    findings_dir = spec_dir / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    (findings_dir / "triage_report.json").write_text(render_json(empty_report))
    (findings_dir / "triage_report.md").write_text(render_markdown(empty_report))


# ─── Auto-fire scheduler ─────────────────────────────────────────────────

_BG_TRIAGER_TASKS: set[asyncio.Task] = set()


def schedule_triager(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget Triager, gated by ``PFACTORY_AUTO_TRIAGE``.

    Default ON (env var unset or "1"). Test fixtures should set
    ``PFACTORY_AUTO_TRIAGE=0`` to keep evaluator's success path from
    auto-advancing.
    """
    if os.environ.get("PFACTORY_AUTO_TRIAGE", "1") == "0":
        return None
    task = asyncio.create_task(run_triager(spec_dir, project_dir, mode=mode))
    _BG_TRIAGER_TASKS.add(task)
    task.add_done_callback(_BG_TRIAGER_TASKS.discard)
    return task
