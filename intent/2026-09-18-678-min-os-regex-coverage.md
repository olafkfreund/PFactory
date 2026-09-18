---
status: draft
issue: 678
author: Olaf Krasicki-Freund
---

# Intent: OS-floor phrasing is not recognised as covering min-os-versions

## Problem

For mobile plans, PFactory injects implicit acceptance criteria unless the
epic already covers them. Coverage is plain substring matching
(`missing_requirements`, `apps/backend/plan/decompose/implicit_requirements.py:269`):
a requirement is covered if any of its keywords appears in a child AC or body.

`min-os-versions` has keywords like "minimum os", "deployment target",
"api level". A brief that states the floor the way people actually write it —
"we support iOS 16 and above", "Android 10 or newer", "iOS 15+" — matches none
of them, so a near-duplicate "Minimum supported OS versions are declared…" AC
is injected next to the one the author wrote.

It cannot be fixed with another substring: the only candidate ("support ios")
also matches "The app supports iOS and Android", which appears in almost every
mobile brief, and would silently stop min-os injection everywhere — the
false-cover direction, which nothing downstream recovers from. Two tests pin
exactly this trade (`tests/test_implicit_requirements.py::test_min_os_and_forced_upgrade_overlap_phrasing`
and `::test_supports_ios_and_android_does_not_cover_min_os`).

Impact is low: injection happens before the completeness lens and readiness
check, so this never fails a gate — it adds a redundant AC.

## Proposed outcome

- "iOS 16 and above", "Android 10 or newer", "iOS 15+", "at least iOS 16" count
  as covering `min-os-versions`; no duplicate AC is injected.
- Naming platforms without a floor ("supports iOS and Android", "tested on
  iOS 17") still does **not** count — the existing guard test stays green.
- The first pinned test flips its `min-os-versions in missing` assertion, as
  the issue anticipates.

## Affected users and systems

- Mobile-app plans (the only plan type with `min-os-versions`).
- `plan/decompose/implicit_requirements.py`; the completeness lens and the
  `service-requirements-covered` readiness check consume the same matcher.
- `tests/test_implicit_requirements.py`.

## Constraints

- Precision over recall: a phrase that might not be a floor must not count.
  Missing a floor costs a duplicate AC; a false cover silently drops a
  requirement.
- Word boundaries (lessons of #397 "rust" in "untrusted", #673 "form" in
  "platform"): "radios 5 and up" must not match.
- No change to any other requirement's matching.

## Open questions

None.
