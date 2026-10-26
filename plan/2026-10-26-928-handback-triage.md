---
status: draft
issue: 928
spec: spec/2026-10-26-928-handback-triage.md
coders:
  1: codex
  3: codex
---

# Plan: the handback sender sends a valid triage block

Coders: the lane brief for this wave names `codex-implement` as the implementer; step 2 is a byte copy, done by the session.

## Approved decisions

- `send_correction` adds `triage = {"contract_version": "handback-triage-1", **request.to_dict()}` to the payload, with `None` values dropped from `triage["aifactory"]`. `default_sender` puts `triage` in the POST body. `fix_request_md`, `source` and `confirm: true` stay.
- No synthetic failing-test entry: a sent request always has a real failing test or `has_visual_plan: true`.
- No `correlation_key` / `manifest_hash`.
- The contract is vendored byte-identical from AIFactory dev @ 74b4b9b9bed20c5d492d3e8a7b5067b0deb4bc2a and checked with `jsonschema` and the vendored `validate_triage_report`.

## Steps

1. `apps/backend/agents/handback/send.py`:
   - `send_correction` (payload dict near the end): build the `triage` dict above and add `"triage": triage` to `payload`.
   - `default_sender` (the `json.dumps({...})` body): add `"triage": payload.get("triage")`; update the docstring's payload key list.
   - Do not change the `handback_request.json` artifact.

   Check: `apps/backend/.venv/bin/python -m pytest tests/test_handback_send.py -q` passes.
   Traps: stdlib only; keep the best-effort `try/except` around the send.
2. Vendor, byte-identical:
   `git -C <AIFactory clone> show 74b4b9b9bed20c5d492d3e8a7b5067b0deb4bc2a:apps/backend/qa/handback_triage.schema.json > tests/contracts/aifactory-handback-triage.schema.json`
   and `...:apps/backend/qa/handback_contract.py > tests/contracts/aifactory_handback_contract.py`.

   Check: `sha256sum` matches the AIFactory blobs.
   Traps: do not reformat the vendored files (ruff may lint the `.py`; exclude it via a `# ruff: noqa` only if pre-commit fails, and record that as a deviation).
3. Add `tests/test_handback_triage_contract.py`: module docstring names the AIFactory paths and SHA and says to re-vendor on contract change. Load the schema with `json`, load the validator with `importlib.util.spec_from_file_location` from `tests/contracts/aifactory_handback_contract.py`. Parametrize over a triage request (one `reject` verdict) and a visual-only request (`visual_correction_plan="..."`, no rejects), with a snapshotter-shaped source whose `api_url` is `None` for the visual case. Send via `send_correction(..., dry_run=False, confirm=True, sender_fn=capture)`; assert `confirm is True`, `fix_request_md.strip()`, no `Draft202012Validator` errors on `payload["triage"]`, and `validate_triage_report(payload["triage"]).ok`. Also assert `default_sender` puts `triage` in the body (monkeypatch `urllib.request.urlopen`).

   Check: the test passes, and fails with step 1 reverted.
   Traps: `jsonschema` is in `apps/backend/requirements.txt`; use `pytest.importorskip("jsonschema")` like CFactory.

## Tests

- `apps/backend/.venv/bin/python -m pytest tests/test_handback_triage_contract.py tests/test_handback_send.py -q`
- `ruff check` on touched files; pre-commit runs the suite and the ratchet.

## Rollback

Revert the PR. Sending is off by default, so nothing deployed changes.
