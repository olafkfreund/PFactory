---
status: draft
issue: 1053
spec: spec/2026-11-18-1053-cfactory-source-channel.md
coders:
  1: lead
  2: codex
  3: lead
reviewers:
  - codex
---

# Plan: accept CFactory as a planning source channel

Decisions: add `cfactory` to `SourceChannel` only (no CFactory or hub change); trust stays
`trusted`; log a sanitised diagnostic (class + pydantic loc/type; other ValueErrors class-only) before the
unchanged sanitised 400; test accept + reject + log.

## Steps

1. `apps/backend/plan/models.py:24`: add `"cfactory"` to the Literal; add cfactory to the
   docstring list at ~188. Reason: one-token edit, lead does it.
   → verify: `python -c` import, step 3 tests.
2. `apps/web-server/server/routes/plan_pipeline.py` `ingest_text` (`except ValueError`):
   add `logger.warning("plan ingest-text rejected: %s", _rejection_reason(exc))` with a small
   module helper per spec item 3; response unchanged. Reason: contained edit, codex.
   → verify: step 3 tests.
3. New `tests/test_plan_ingest_cfactory_channel.py` (model the app fixture on
   `tests/test_plan_session_org_scope.py`): accepted `cfactory` -> 200 and
   `SERVICE`/service session `source_channel == "cfactory"`; `bogus` -> 400 `invalid request`;
   `caplog` shows the warning and not the request text; a monkeypatched non-pydantic ValueError with a sentinel is never logged (class only); `content_trust == "trusted"`. Reason: lead writes tests.
   → verify: `apps/backend/.venv/bin/pytest tests/test_plan_ingest_cfactory_channel.py -q`.
   Traps: do not log request body; keep detail `invalid request`.

## Tests

`apps/backend/.venv/bin/pytest tests/ -m "not slow" -q` (or the touched plan/routes subset plus
the new file), `ruff check` and `ruff format --check` on touched files.

## Rollback

Revert the commit; CFactory planning dispatch returns to HTTP 400.
