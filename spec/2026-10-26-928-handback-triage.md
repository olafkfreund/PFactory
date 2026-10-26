---
status: draft
issue: 928
intent: intent/2026-10-26-928-handback-triage.md
---

# Spec: the handback sender sends a valid triage block

## Design

In `apps/backend/agents/handback/send.py`, `send_correction` builds a `triage`
block from the `CorrectionRequest` it already holds, as TFactory's sender does
(TFactory `agents/handback/send.py`):

```python
triage = {"contract_version": "handback-triage-1", **request.to_dict()}
```

`request.to_dict()` already has the contract's fields: `source`
(`triage` | `visual_inspection`), `aifactory_task_id`, `aifactory`,
`failing_tests` (each with a non-empty `test_id` and `reason`, from the
Evaluator's `reject` verdicts) and `has_visual_plan`. The `aifactory` envelope
drops `None` values, because the schema types its keys as strings and the
snapshotter writes `api_url: None` when it has no target.

The payload gains `triage`; `default_sender` puts it in the POST body next to
`fix_request_md`, `source` and `confirm: true` (already sent). Those are every
field `ApplyCorrectionRequest` requires or that the triage gate needs.

Synthetic fallback: not needed. `send_correction` returns before sending when
`nothing_to_hand_back`, so a sent request has either at least one real failing
test or `has_visual_plan: true`, and both satisfy the schema's `anyOf` and
`validate_triage_report`. No synthetic entry is added.

Contract test `tests/test_handback_triage_contract.py`, against two files
vendored byte-identical from AIFactory `origin/dev`
@ 74b4b9b9bed20c5d492d3e8a7b5067b0deb4bc2a (the SHA CFactory pinned in #499):

- `tests/contracts/aifactory-handback-triage.schema.json` ← `apps/backend/qa/handback_triage.schema.json`
- `tests/contracts/aifactory_handback_contract.py` ← `apps/backend/qa/handback_contract.py` (stdlib-only)

The test sends a triage request and a visual-only request through
`send_correction` with a fake sender, then checks the captured payload: the
`triage` block has no `jsonschema` Draft 2020-12 errors, `validate_triage_report`
returns `ok`, `confirm is True`, and `fix_request_md` is non-empty.

## Alternatives rejected

- A synthetic `failing_tests` entry for every send (CFactory's shape): PFactory has real failures; a synthetic one would hide them.
- Importing the validator from a sibling AIFactory checkout: CI has none, so the check would skip there.
- Adding `correlation_key` / `manifest_hash`: optional, and PFactory's handback has neither today.

## Risks

- The vendored copy drifts from AIFactory: re-vendor when AIFactory changes the contract, as CFactory does.
- Send stays off by default, so no deployed behaviour changes.

## Verification

- `pytest tests/test_handback_triage_contract.py tests/test_handback_send.py` passes.
- The new test fails on `origin/dev` (no `triage` in the payload).
