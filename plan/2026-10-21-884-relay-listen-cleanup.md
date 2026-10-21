---
status: approved
issue: 884
spec: spec/2026-10-21-884-relay-listen-cleanup.md
---

# Plan: close the relay listener connection when LISTEN fails, and dedup the oversized-drop warning

Approved decisions (from the spec):

- `add_termination_listener` and `add_listener` move inside `_listen_on`'s
  existing `try`; `add_listener` is bounded by a new `_LISTEN_TIMEOUT = 5.0`.
- Failures propagate to `_run_listener`, which reconnects as today.
- New `_oversized_drop_warned` flag, same pattern as `_outbox_full_warned`,
  reset on a successful encode.

## Steps

1. `apps/web-server/server/websockets/relay.py`: add `_LISTEN_TIMEOUT` next to
   `_STOP_TIMEOUT`; in `_listen_on` move both listener registrations into the
   `try` and wrap `add_listener` in `asyncio.wait_for`.
   → `pytest tests/test_ws_relay.py -q`
2. Same file: add `_oversized_drop_warned` with a one-line comment, dedup the
   warning in `publish()`, reset it after a successful `_encode`.
   → same pytest command.
3. `tests/test_ws_relay.py`: reset `_oversized_drop_warned` in the autouse
   fixture; add tests for a raising `add_listener` (connection closed), a
   hanging `add_listener` (bounded, closed), and the warning dedup + reset.
   → `pytest tests/test_ws_relay.py -q`; `ruff check` and `ruff format --check`
   on both files.

## Tests

The three tests in step 3 fail on origin/dev and pass after steps 1-2.

## Rollback

Revert the PR; no data or schema change.
