---
status: draft
issue: 999
author: olafkfreund
---

# Intent: make test_plan_docs_p4 hermetic

## Problem

`test_emit_docs_connections_path_always_writes_repo` passes `base_url: https://bs` with no HTTP stub, so `BackstageTarget` does a real DNS lookup. On a slow resolver it hits the 120 s timeout and turns `tests/postgres/test_p1_suite_against_postgres.py` red.

## Proposed outcome

No unit test in that file depends on DNS or the network. The test still asserts the repo doc is written, a backstage result is present, and `registry.json` lands on disk.

## Affected users and systems

PFactory tests only.

## Constraints

Test-only change; no production code edits.

## Open questions

None.
