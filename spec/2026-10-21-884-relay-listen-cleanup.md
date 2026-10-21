---
status: draft
issue: 884
intent: intent/2026-10-21-884-relay-listen-cleanup.md
---

# Spec: close the relay listener connection when LISTEN fails, and dedup the oversized-drop warning

## Design

### LISTEN inside the cleanup scope

Move `conn.add_termination_listener(_on_terminate)` and the `add_listener`
await inside `_listen_on`'s existing `try:`, so its `finally` (bounded
`remove_listener` + `close`, both under `suppress(Exception)`) always runs.
Wrap `add_listener` in `asyncio.wait_for(..., timeout=_LISTEN_TIMEOUT)`, a new
module constant of 5.0 s like the other relay timeouts. A timeout raises out of
`_listen_on`; `_run_listener` already logs any `Exception` and reconnects with
backoff. `remove_listener` for a listener that was never added is suppressed;
`remove_termination_listener` on asyncpg is a set discard.

### Dedup the oversized-drop warning

Module flag `_oversized_drop_warned = False`, used like `_outbox_full_warned`:
warn only when the flag is false and set it; reset it when `_encode` succeeds,
so a later, separate run of oversized events warns again.

## Alternatives rejected

- Timeout only, without moving into `try`: still leaks on a raising
  `add_listener`.
- Rate limiting by time: the module already dedups by flag everywhere.

## Risks

- A LISTEN slower than 5 s on a healthy but slow database now reconnects
  instead of waiting. Same bound as acquire/execute; acceptable.

## Verification

- Test: fake connection whose `add_listener` raises; `_listen_on` raises and
  `close()` was awaited. Fails on origin/dev (close never called).
- Test: `add_listener` hangs; `_listen_on` returns/raises within the timeout
  and closes the connection.
- Test: three oversized publishes log one warning; a normal publish then an
  oversized one logs a second. Fails on origin/dev (three warnings).
- `pytest tests/test_ws_relay.py`, ruff on the changed files.
