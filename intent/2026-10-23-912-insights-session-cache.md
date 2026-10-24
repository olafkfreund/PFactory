---
status: approved
issue: 912
author: olafkfreund
---

# Intent: insights chat keeps every message across pods

## Problem

`InsightsService` caches each chat session per process and never re-reads the session file. With two pods sharing the PVC, a pod holding a stale copy appends a message to it and overwrites the file, which drops the messages the other pod wrote.

## Proposed outcome

With any number of pods, every message sent in a session stays in its history.

## Affected users and systems

- `apps/web-server/server/services/insights_service.py`.
- Users of the Insights chat.
- factory-gitops#273, which raises PFactory to 2 replicas.

## Constraints

- The session file format does not change.
- No new dependency.

## Open questions

None.
