---
status: draft
issue: 1053
intent: intent/2026-11-18-1053-cfactory-source-channel.md
---

# Spec: accept CFactory as a planning source channel

## Design

1. `apps/backend/plan/models.py:24`: add `"cfactory"` to `SourceChannel`. This is the only
   enumeration of channels: grep of openapi.yaml, frontend (`channel?: string`), web-server
   (`channel: str`), MCP/CLI, registry and the emit contracts found no other list
   (`extension-registry.json` already names `cfactory` as an owner service). No DB column or
   enum constrains it. No hub contract change, so no Factory PR.
2. `models.py:~193` content trust: unchanged (`cfactory` falls in the `trusted` branch); update
   the docstring list to include cfactory.
3. `plan_pipeline.py` `ingest_text`: in `except ValueError`, `logger.warning` a diagnostic then
   raise the same sanitised 400. The log carries the exception class and, for pydantic
   `ValidationError`, only each error's `loc` and `type` (never `input`, which can hold request
   text); for any other ValueError only the class name (a message can carry request text). No body, headers or tokens.
4. Tests: ingest-text with `channel: "cfactory"` returns 200 and the stored session has
   `source_channel == "cfactory"`; channel `"bogus"` returns 400 with detail `invalid request`
   and the warning is logged without the text body; a non-pydantic ValueError carrying a sentinel string is logged class-only; the session's `content_trust` is `trusted`.

## Alternatives rejected

- Change CFactory to send `agent`: loses the origin in audit and plan docs, and touches a second repo.
- Loosen `SourceChannel` to `str`: drops validation at a trust boundary.
- Log `str(exc)` or a truncated message: pydantic messages embed `input_value`, and truncation keeps sensitive leading text (Codex review).

## Risks

Low. A new literal value only widens accepted input to a known caller. Log volume: one line per
rejected request.

## Verification

New tests pass; `tests/` backend suite (not slow) and ruff check/format pass; unknown channel still 400.
After release, verifying live needs a hard card via `cfactory_code_card` or a direct ingest-text
call, because low and medium cards bypass PFactory.
