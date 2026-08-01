# Task Contract v2 (skip-planning handoff)

The **Task Contract v2** is PFactory's signed, canonical handoff to AIFactory. It
is a superset of AIFactory's `implementation_plan.json` that adds *execution* and
*tfactory* profiles, so when the signature and completeness verify, AIFactory
**skips its own planner** and goes straight to the wave executor — and TFactory
gets a ready-made verify profile.

- **Spec:** [RFC-0002](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0002-task-contract.md)
- **Schema (vendored):** `apps/backend/plan/emit/contracts/task-contract.schema.json`
- **Epic:** [#65](https://github.com/olafkfreund/PFactory/issues/65)

!!! note "Where it sits"
    The classic emit path renders GitHub epics + child issues
    (`plan/emit/github_emitter.py`) and the lightweight `requirements.json`
    handoff (`plan/emit/aifactory_handoff.py`). The Task Contract v2 emitter is
    the **richer, signed** path: one contract carrying the whole plan + how to
    build it + how to test it.

## How it is assembled

Each block is built by a focused, deterministic module and composed by
`plan/emit/contract_emit.py`:

| Stage | Module | Produces |
|-------|--------|----------|
| Plan | `contract_builder.build_task_contract` | `phases` → `subtasks` (dependency layers) from the `EpicPlan` graph, `final_acceptance`, `workflow_type` |
| Execution | `execution_profile.build_execution` | `execution`: complexity, model, provider (via `infer_provider_from_model`), per-phase models/thinking, `parallel`/`workers`, `skip_planning` |
| Review tier | `review_tier.derive_review_tier` | `execution.review_tier` (auto/async/blocking) from the governance `PlanReview` |
| Verification | `verification.attach_verification` | per-subtask `verification` specs + global `required_commands` |
| TFactory | `tfactory_block.build_tfactory` | `tfactory`: lanes, frameworks, endpoints, coverage target, `ac_to_code_map` |
| Sign | `signing.attach_signature` | `approval`: HMAC `trusted_plan` envelope |
| Validate + emit | `contract_emit.emit_contract` | validates, signs, POSTs to `/api/tasks/from-plan` |

```python
from plan.emit.contract_emit import emit_contract

result = emit_contract(
    plan, epic, review,
    base_url="http://localhost:3101",   # PFACTORY_AIFACTORY_API_URL
    project_id="my-project",
    dry_run=True,                        # default — nothing is POSTed
)
# result = {ok, dry_run, signed, endpoint, contract, ...}
```

## Validation

Every emitted contract is validated against the vendored schema by
`plan/emit/task_contract.py` before it leaves the process — an invalid contract is
**never POSTed**. Validation uses `jsonschema` (draft-2020-12) when installed and
falls back to a dependency-free structural validator otherwise, so the gate always
runs.

## Signing (trust envelope)

The contract is signed with an HMAC-SHA256 `trusted_plan` envelope that mirrors
AIFactory byte-for-byte, so it verifies on the AIFactory side and unlocks the
skip-planning fast-path.

- **Key:** `AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY` (authority `pfactory`). Never
  logged or embedded.
- The canonical payload is the contract minus its `approval` block, joined with
  the approval metadata.

### Key ids and rotation (#401)

The envelope carries an optional `kid` (key id), bound into the signed bytes.
It is what AIFactory's `AIFACTORY_TRUSTED_PLAN_RETIRED_KIDS` names when
revoking a key: **without a `kid` a leaked key can only be answered by rotating
the secret in place, which invalidates every in-flight approved contract at
once.**

Which key signs, and whether a `kid` is stamped, is decided entirely by the
environment:

| Environment | Envelope | Notes |
|-------------|----------|-------|
| `AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY` only | no `kid` (4 fields) | Legacy. Byte-identical to before #401; verified against AIFactory's unkeyed authority entry. |
| one `AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__<KID>` | `kid: <kid>` (lowercased) | Revocable. The keyed var wins over the legacy var. |
| several `..._PFACTORY__<KID>` vars | — | Ambiguous: raises unless `PFACTORY_TRUSTED_PLAN_KID` picks one. |
| `PFACTORY_TRUSTED_PLAN_KID` with no matching keyed var | — | Raises. Falling back to the legacy key would silently emit an unrevocable contract. |
| no key at all | none | Contract emitted unsigned (AIFactory then plans normally). |

**Deployment ordering matters.** AIFactory must hold
`AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__<KID>` *before* PFactory starts signing
with that kid, or every new contract is rejected with `no verification key for
authority 'pfactory' key-id ...`. Keep the legacy var configured on AIFactory
throughout so contracts already in flight (signed with no `kid`) keep verifying.
Retire the old kid only once those have drained.

## Emit transport

`emit_contract` POSTs to `{PFACTORY_AIFACTORY_API_URL}/api/tasks/from-plan`. If the
fast-path is unavailable it **falls back** to the legacy `create-and-run`
requirements path for the first child (AIFactory then plans normally). Dry-run is
the default, honouring the no-automatic-pushes policy.

## Bidirectional sync + handback

The reverse direction (`plan/emit/contract_sync.py`) consumes the **RFC-0001
completion events** AIFactory and TFactory emit, reconciles them on the shared
`correlation_key`, and flags units needing a **handback**:

- `parse_completion_event` validates the inbound envelope.
- `classify_outcome` maps a service's status → success / failure / in_progress.
- `ContractSyncRegistry.apply(event)` tracks per-key state;
  `needing_handback()` surfaces units where a downstream failure (AIFactory
  `qa_failed`, TFactory `rejected`, …) means the plan/code must be revised.

This pairs with PFactory's outbound terminal event (`plan/completion.py`) to close
the **plan → build → test → (handback)** loop across the suite.

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `PFACTORY_AIFACTORY_API_URL` | `http://localhost:3101` | AIFactory base URL for `/from-plan` |
| `AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY` | _(unset)_ | Legacy HMAC signing key (no `kid`); without it and without a keyed var the contract is emitted unsigned |
| `AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__<KID>` | _(unset)_ | Keyed HMAC signing key; signs with `kid=<kid>` so the key is individually revocable (#401). Wins over the legacy var |
| `PFACTORY_TRUSTED_PLAN_KID` | _(unset)_ | Picks which `kid` to sign with when several keyed vars are set. Unnecessary with exactly one |
| `PFACTORY_AIFACTORY_CONTRACT_VERSION` | `1` | Gates the v2 additive keys on the lightweight handoff |
