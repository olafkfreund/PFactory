---
status: approved
issue: 682
author: Olaf Krasicki-Freund
---

# Intent: The registry shows a toggle on a lens it will never switch off

## Problem

`default_lenses()` (`apps/backend/plan/review/lenses/base.py:45`) always runs
the mandatory lenses in its hardcoded `order` (feasibility, architecture,
security, compliance, best-practices, completeness). The registry's `enabled`
flag is consulted only for lenses *outside* that list (`_gated_off`, #676). This
is deliberate and right: `is_enabled()` treats an unreadable registry as
"disabled", so honouring it for the mandatory set would let a parse error
silently empty the whole review.

But the registry does not say so. Measured on `dev`:

- Of the six mandatory lenses, exactly one has a registry entry:
  `compliance-review` (`"enabled": true`). The other five have none. (The issue
  says "six built-ins"; it is one.)
- Setting `compliance-review` to `"enabled": false` changes nothing — the
  compliance lens still runs — while `GET /api/plan/registry` serves the entry
  with `enabled: false`, and the registry's own description calls the pipeline
  "toggleable". An operator who flips it and trusts the audit surface believes
  compliance review is off when it is on.

The file (`apps/backend/plan/emit/contracts/extension-registry.json`) is a
byte-identical copy of the Factory hub's `apis/extension-registry.json`; no
other factory carries a copy.

## Proposed outcome

- The `compliance-review` entry no longer presents a settable `enabled` flag;
  it says it is mandatory (the issue's option 2: remove the class, not document
  it).
- The registry description says `enabled` gates optional extensions only;
  mandatory review lenses always run.
- If anyone ever writes `enabled: false` on a mandatory lens's entry, it is
  caught — a test fails in this repo, and the running service logs a warning —
  rather than discovered at audit.
- The Factory hub copy carries the same change, so the two files stay identical.

## Affected users and systems

- Operators and auditors reading `GET /api/plan/registry` / the registry file.
- `apps/backend/plan/emit/contracts/extension-registry.json`,
  `apps/backend/plan/review/lenses/base.py` (docstring + warning), tests.
- Factory hub `apis/extension-registry.json` (separate repo, separate PR).

## Constraints

- The mandatory review must stay impossible to switch off via the registry —
  no behaviour change to which lenses run.
- Consumers ignore unknown keys (per the registry's description), so adding a
  field is safe; removing `enabled` from one entry must not break a consumer
  that reads it (checked in the spec).

## Open questions

1. Scope of the hub change: open the matching Factory PR from this work
   (recommended — otherwise the "identical copy" silently diverges), or change
   only PFactory's copy and file a hub issue?
