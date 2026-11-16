---
status: approved
issue: 999
spec: spec/2026-11-16-999-hermetic-docs-p4.md
coders:
  1: you
reviewers:
  - codex
---

# Plan: make test_plan_docs_p4 hermetic

Stub `_default_http` in the one test (spec decision); keep assertions.

## Steps

1. tests/test_plan_docs_p4.py (`test_emit_docs_connections_path_always_writes_repo`): add `monkeypatch` fixture and `monkeypatch.setattr("plan.emit.docs.targets.backstage._default_http", lambda method, url: 200)` → verify by `pytest tests/test_plan_docs_p4.py`
   Traps: none.

## Tests

`pytest tests/test_plan_docs_p4.py -q`: 9 passed. `ruff check` and `ruff format --check` on the file.

## Rollback

Revert the commit.
