---
status: approved
issue: 960
author: olafkfreund
---

# Intent: never emit a bare issue number as the correlation key

## Problem

`origin_key()` in `apps/backend/plan/completion.py` returns `str(n)` when the
repo is unknown, for both the origin issue and the emitted epic in
`correlation_key_for`. Since CFactory #506 keys match exactly, so issue 7 in
two repos would share one CFactory work item.

## Proposed outcome

PFactory never mints a bare number. An issue without a known repo correlates on
the synthetic `pf-<session_id>`.

## Affected users and systems

PFactory plan sessions, completion events, MCP status, the contract key handed
to AIFactory/TFactory.

## Constraints

`<repo>#<n>` unchanged; a key already persisted on a session is not re-keyed
(existing behaviour); no CFactory change.

## Open questions

None.
