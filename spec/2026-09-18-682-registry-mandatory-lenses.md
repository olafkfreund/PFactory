---
status: approved
issue: 682
intent: intent/2026-09-18-682-registry-mandatory-lenses.md
---

# Spec: The registry shows a toggle on a lens it will never switch off

## Facts established

- Two different registries exist in PFactory. `GET /api/plan/registry` serves the
  YAML catalogue (`plan/registry/catalogue/`): providers, adapters, connectors,
  skills, one template — **no lenses**. The lens-gating registry is
  `plan/emit/contracts/extension-registry.json`, read by
  `plan/review/extension_registry.py` and not served over HTTP. So the
  misleading surface is the JSON file itself (in both repos) and the hub's
  loader script, not an API.
- In that JSON, `compliance-review` is the only entry for a mandatory lens.
- The Factory hub copy (`apis/extension-registry.json`) is byte-identical. The
  hub loader (`Factory/scripts/extension_registry.py`) validates only
  `name`/`category`/`effect`, and `enabled_only()` keeps entries whose
  `enabled is True`. **Removing `enabled` from `compliance-review` would drop it
  from `enabled_only()`**, so the entry keeps `"enabled": true`.

## Design

### Registry JSON (PFactory copy and hub copy, kept identical)

- `compliance-review` gains `"mandatory": true` and keeps `"enabled": true`;
  its description gains one sentence: it always runs; the review cannot be
  switched off here.
- The top-level `description` gains one sentence: `enabled` gates optional
  extensions; an entry with `mandatory: true` always runs and must stay
  `enabled: true`.

### PFactory `apps/backend/plan/review/lenses/base.py`

- `default_lenses()` docstring states the asymmetry: the `order` list is the
  mandatory set and never consults the registry (an unreadable registry must
  not empty the review); `enabled` is load-bearing only for gated lenses in the
  tail.
- New `_warn_if_mandatory_disabled(order)`, called from `default_lenses()`: for
  each lens in the mandatory `order`, if `get_extension(f"{lens}-review")`
  exists and its raw `enabled` is `False` or it lacks `mandatory: true`, log a
  WARNING naming the entry ("registry entry X marks a mandatory lens … it still
  runs"). Logged once per process (a module-level set). Never raises; never
  changes the returned list.

### Guards

- PFactory test: every registry entry whose name is `<lens>-review` for a lens
  in the mandatory set has `mandatory: true` and `enabled: true`; the scan is
  not vacuous (finds `compliance-review`).
- PFactory test: with a temp registry (`PFACTORY_EXTENSION_REGISTRY`) where
  `compliance-review` is `enabled: false`, `default_lenses()` still returns the
  compliance lens and a WARNING is logged.
- Hub: `validate_entry` adds one rule — `mandatory: true` with `enabled` not
  `True` is an error — and the script's built-in self-check gains a case for it.

## Alternatives rejected

- **Omit `enabled` on mandatory entries** (the issue's other option-2 form):
  silently removes `compliance-review` from the hub's `enabled_only()`.
- **Honour `enabled` for mandatory lenses**: an unreadable registry would empty
  the review — rejected in the issue itself.
- **Docs only (option 1)**: leaves a settable-looking field with no guard.
- **Add entries for the other five mandatory lenses**: no one asked for them;
  they would be new surface to keep in sync. The guard covers any that are
  added later.

## Risks

- Hub PR and PFactory PR must land together or the copies differ briefly;
  each PR links the other.
- A consumer that rejects unknown keys would choke on `mandatory` — the
  registry declares "consumers ignore unknown keys", and the hub validator does.

## Verification

- `pytest tests/test_red_team_lens.py tests/test_lens_gating.py tests/test_compliance_lens.py`
  + the new tests green in PFactory.
- Negative control: set `compliance-review` to `enabled: false` in the vendored
  JSON → the entry test fails; the warning test proves the lens still runs.
- Hub: `python scripts/extension_registry.py` (self-check) passes; with the
  entry set to `enabled: false` it reports the new error.
- `diff` of the two JSON copies is empty after both changes.
