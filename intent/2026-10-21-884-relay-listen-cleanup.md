---
status: approved
issue: 884
author: olafkfreund
---

# Intent: close the relay listener connection when LISTEN fails, and dedup the oversized-drop warning

Follow-up from the review of PFactory#833 (#804 WebSocket relay).

## Problem

1. `_listen_on` in `apps/web-server/server/websockets/relay.py` registers the
   termination listener and awaits `conn.add_listener(...)` before its `try:`.
   If `add_listener` raises, the `finally` never runs and the connection is
   never closed: one leaked connection per reconnect attempt. `add_listener`
   has no timeout either, so a socket that half-dies between connect and
   LISTEN wedges the listener with no reconnect.
2. `publish()` logs "dropping oversized ... could not fit" once per event, with
   no dedup, unlike every other warning in the module.

## Outcome

- A failed or hung LISTEN closes its connection and the reconnect loop retries.
- A run of oversized events logs one warning; a later run warns again.

## Affected

`apps/web-server/server/websockets/relay.py`, `tests/test_ws_relay.py`.

## Constraints

- Keep the existing reconnect loop, teardown bounds and dedup pattern.
- No behaviour change for a healthy connection.

## Open questions

None.
