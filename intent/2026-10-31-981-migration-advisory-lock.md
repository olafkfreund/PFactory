---
status: draft
issue: 981
author: olafkfreund
---

# Intent: serialise `alembic upgrade head` at boot across pods

## Problem

`init_db()` (`apps/web-server/server/database/engine.py`) runs `alembic upgrade
head` at every boot when `MIGRATIONS_AUTO_APPLY` is true, which is the default.
There is no init container or Job, and nothing serialises the upgrade across
pods. PFactory runs 2 pods (KEDA `minReplicaCount` 2) and may run 4. A rolling
update starts pods one at a time, but a KEDA scale-up, a node restart or a
crash loop can start two at once. Both then run the same upgrade: the second
fails on duplicate DDL or, for a migration with data steps, runs them twice.

## Proposed outcome

Two pods booting at the same time against one Postgres database both start,
and each pending migration is applied exactly once. SQLite (local dev)
behaves as today.

## Affected users and systems

PFactory web-server pods in the cluster (Postgres); local dev (SQLite,
unchanged). AIFactory and TFactory are checked read-only, not changed.

## Constraints

- No new Argo hook Job or gitops change.
- No new dependency. Must not change the `MIGRATIONS_AUTO_APPLY=false` path.
- Must land before 2026-11-02.

## Open questions

None. The user chose a Postgres session advisory lock around the upgrade.
