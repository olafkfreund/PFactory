---
status: draft
issue: 960
spec: spec/2026-10-30-960-repo-scoped-correlation-key.md
---

# Plan: never emit a bare issue number as the correlation key

Decision (approved spec): `<repo>#<n>` when the repo is known, else synthetic
`pf-<session_id>`; a repo-less origin does not fall through to the epic;
persisted keys are not re-keyed.

## Steps

1. `apps/backend/plan/completion.py:62-88`: `origin_key(repo: str, n)` returns
   `f"{repo}#{n}"`; `correlation_key_for` returns `origin_key` only when the
   matching repo is set, else `synthetic_key` (origin branch does not fall
   through to the epic). Update docstrings. Tests: `tests/test_plan_completion.py`
   `test_emitted_epic_key_is_scoped_to_the_repo` (None → `pf-<id>`), add an
   origin-without-repo case; `tests/test_plan_from_issue_route.py`
   `test_non_issue_sessions_keep_the_pre_874_key` sets `emit_repo` or expects `pf-`.
   → verify by `python -m pytest tests/test_plan_completion.py tests/test_plan_from_issue_route.py -q`
   Traps: grep for other `origin_key(` callers and bare-key asserts.

## Tests

Pytest above green; ruff clean.

## Rollback

Revert the PR commit.
