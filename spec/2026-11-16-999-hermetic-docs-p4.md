---
status: draft
issue: 999
intent: intent/2026-11-16-999-hermetic-docs-p4.md
---

# Spec: make test_plan_docs_p4 hermetic

## Design

In the one test, `monkeypatch.setattr` `plan.emit.docs.targets.backstage._default_http` to a stub returning 200. `BackstageTarget.__init__` reads that module attribute at construction, so the stub is used. Assertions are unchanged.

## Alternatives rejected

- `https://bs.invalid`: still goes through the resolver and the error path, and does not stub the network.
- Passing `http=` to the target: `emit_docs` builds targets from connections and has no such hook.

## Risks

None; the production path is untouched.

## Verification

`pytest tests/test_plan_docs_p4.py` passes; ruff clean.
