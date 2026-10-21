---
status: approved
issue: 874
author: olafkfreund
---

# Intent: guard /api/ready against hung checks, and bound the timer period

Follow-up from the review of PFactory#873 (RFC-0024 §4). Neither problem bites
today because no check is `critical`, but both must be fixed before the first
check is promoted.

## Problem

1. `readiness()` (`apps/web-server/server/background/invariants.py:85-93`)
   writes its 10 s cache only after the critical checks return, and
   `/api/ready` (`apps/web-server/server/main.py:488`) runs it through
   `asyncio.to_thread` on every probe. If a critical check hangs, every probe
   starts another thread that also hangs, filling the shared default executor
   and stalling the app's other `to_thread` callers (plan pipeline, GitHub
   routes). The probe itself never gets an answer.
2. `INVARIANTS_PERIOD_SECONDS` (`apps/web-server/server/config.py:97`) has no
   lower bound. `0` or a negative value makes the non-critical loop run back to
   back, burning CPU and logging every violation on every pass.
3. `core/_invariants.py` says core's process-lifetime state is all caches, but
   `fast_mode._fast_mode_atexit_registered` and
   `workspace/setup._git_hook_check_done` are one-shot flags.

## Proposed outcome

- At most one critical-check run is in flight at any time. Concurrent probes
  share it, or get the cached result.
- A critical run that has not finished within a bound well below the 5 s probe
  timeout counts as a violation naming the package(s), so `/api/ready` answers
  503 rather than hanging.
- The timer period cannot be set below 30 s.
- core's declared-empty reason mentions the one-shot flags.

## Affected users and systems

PFactory web server (`/api/ready`, invariant timer, settings) and the core
package's invariant declaration. `/api/health` does not change.

## Constraints

- `/api/health` must not change (it is the liveness probe).
- Nothing becomes critical in this change.
- No network calls in critical checks (unchanged).

## Open questions

None.
