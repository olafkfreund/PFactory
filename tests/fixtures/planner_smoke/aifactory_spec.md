# Add session expiry to the auth module

> Spec ID: 042-session-expiry
> Status: shipped — ready for PFactory

## Overview

Sessions currently live forever. Add a TTL with an explicit expiry
timestamp; reject expired sessions on lookup; refresh sessions that
are within a grace window of their expiry.

## Acceptance Criteria

- **AC#1** — `login_user(email, password)` returns a session whose
  `expires_at` is exactly 24 hours after creation. Existing happy-path
  behaviour (session object shape, return type) is unchanged.
- **AC#2** — `get_session(session_id)` returns `None` for an expired
  session and removes it from the session store. No exceptions.
- **AC#3** — `refresh_session(session_id)` extends `expires_at` by
  another 24 hours, but only if the session is within the last 5 min
  of its TTL ("grace window"). Outside the grace window, returns the
  unmodified session.

## Out of Scope

- Sliding-window refresh on every access (deferred to a later spec)
- Session encryption or rotation
- Multi-device session lists

## Expected Deliverable

The three functions above behave per the criteria; the existing
`logout_user` continues to work; no regressions in unrelated auth
flows.
