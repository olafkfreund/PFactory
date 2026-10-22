---
status: approved
issue: 807
intent: intent/2026-09-29-807-oauth-state-shared.md
---

# Spec: email OAuth state and the GitHub device flow work, or fail honestly, with more than one replica

Decisions carried from the approved intent:

1. The email OAuth state goes in a database table, consumed with
   `DELETE ... RETURNING`.
2. The GitHub device flow refuses when more than one replica is possible.

## Design

### Facts from the code (`origin/dev`, 2026-09-29)

- `routes/email.py` already reaches the database through
  `async_session_factory()` (the `EmailAccount` reads and writes at `:156`,
  `:432`, `:655`), so a state table needs no new plumbing.
- The state is written at `:287` (Outlook) and `:502` (Gmail) and popped at
  `:339` and `:556`. The value holds `user_id`, `provider`, `created_at` and
  `origin`, and `_cleanup_expired_states()` sweeps entries older than 600 s.
- **Neither callback checks that the state was issued for its own
  provider.** A Gmail state is accepted by the Outlook callback. It is not
  exploitable on its own (the state is secret and bound to the user), but the
  new consume step binds the provider for free.
- `routes/github.py`: `/auth/start` (`:827`) already returns
  `{"success": True, "data": {"success": False, "message": ...}}` when `gh`
  is missing. `GitHubOAuthFlow.tsx:226-235` shows `data.message` for that
  shape, so a refusal in the same shape needs **no frontend change**.
- The replica signal `PFACTORY_REPLICA_COUNT` is parsed inline in
  `plan/service.py:444` (a non-integer is treated as 1). There is no shared
  helper.
- SQLite 3.53 and SQLAlchemy 2.0.51 in the venv support `DELETE ...
  RETURNING`, and so does Postgres.
- The Alembic head is `e5b8c3f1a7d2` (#806).

### The state table

A new model `OAuthConnectState`, table `oauth_connect_states`:

| column | type | note |
|---|---|---|
| `state` | `String(64)` PK | `secrets.token_urlsafe(32)` (43 chars) |
| `user_id` | `String(36)` | no FK: `_get_user_id` returns a fixed id when auth is disabled |
| `provider` | `String(16)` | `outlook` / `gmail` |
| `origin` | `String(255)`, nullable | the opener origin (#541) |
| `expires_at` | `DateTime(timezone=True)` | now + 600 s, indexed |

It holds no credential, as today.

The migration (`down_revision = "e5b8c3f1a7d2"`) creates the table and the
`expires_at` index; downgrade drops it.

### Two helpers in `routes/email.py`

These replace the dict and `_cleanup_expired_states`:

- `_save_connect_state(user_id, provider, origin) -> str`: in one
  transaction, it deletes rows with `expires_at <= now` (the sweep the old
  cleanup did), inserts the new row, and returns the token.
- `_consume_connect_state(state, provider) -> dict | None`: runs
  `DELETE FROM oauth_connect_states WHERE state = :state AND provider =
  :provider AND expires_at > now RETURNING user_id, origin`, then commits.
  - One statement, so it is single-use across pods: a second consume, or a
    concurrent one on another pod, gets no row.
  - A wrong provider or an expired state gets None, and the callback returns
    the existing "Invalid or expired OAuth state" page unchanged.

The start and callback routes call these in place of the dict operations.
Everything else in the callbacks is unchanged.

### The device flow (`routes/github.py`)

- A small `_replica_count()` parses `PFACTORY_REPLICA_COUNT`, with the same
  rule as `plan/service.py` (a missing or bad value means 1). It is a local
  copy; see Alternatives.
- `/auth/start`: if `_replica_count() > 1`, return before spawning `gh`:

  ```json
  {"success": true, "data": {"success": false, "message": "GitHub sign-in from the portal is disabled when PFactory runs more than one replica: the credential would exist on one pod only. Set GITHUB_TOKEN in the PFactory Secret instead."}}
  ```

- `/auth/status` is unchanged (with no flow started it reports not
  complete, as today).
- With one replica (today's production), nothing changes.

## Alternatives rejected

- **A stateless signed token:** rejected in the intent; it is replayable
  until it expires.
- **Keeping the dict and adding session affinity:** that does not cover a
  provider redirect, which arrives without the portal's cookie, and it hides
  the problem instead of removing it.
- **A shared replica-count helper module now:** only two readers exist. A
  one-line parse duplicated with a comment pointing at `plan/service.py` is
  smaller than a new module. Extract it when a third reader appears.
- **Persisting the `gh` token into shared storage:** rejected in the intent
  (a security change beyond this issue).

## Risks

- **An in-flight connect during the deploy:** a state issued in memory by
  the old pod is gone after the rollout, and the user retries. There are 0
  email accounts in production, so nobody is mid-connect.
- **Clock skew between pods:** expiry uses each pod's clock. All pods run on
  one node, and the TTL is 10 minutes.
- **Table growth:** abandoned states are swept on every start. Without
  starts they sit there, which is harmless: a few rows, no credential.
- **`PFACTORY_REPLICA_COUNT` unset:** it counts as 1, so the device flow
  stays enabled. factory-gitops#273 must set it (its spec already plans to).

## Verification

- A new `apps/web-server/tests/test_email_oauth_state_shared.py`, using an
  in-memory SQLite with `Base.metadata.create_all` and a patched
  `async_session_factory`, the pattern from `tests/audit`. It checks:
  - a state saved by one "pod" is consumed through a fresh session (the
    other pod) and returns `user_id` and `origin`;
  - a second consume returns None (single-use);
  - an expired state returns None;
  - a state issued for `gmail` is refused by the `outlook` consume;
  - starting a flow sweeps expired rows.
- A device-flow test (same file or `test_github_auth_replicas.py`): with
  `PFACTORY_REPLICA_COUNT=2`, `POST /github/auth/start` returns the refusal
  and never calls `create_subprocess_exec`. With it unset, the refusal is
  absent (`gh` is monkeypatched).
- Run the new tests on the current code first: they fail (there is no table,
  and there is no refusal).
- Postgres: `tests/postgres` `alembic upgrade head` covers the new
  migration, plus one consume-race test (2 concurrent consumes of the same
  state, exactly one wins).
- Gates:
  - ci.yml per-process;
  - store mode;
  - `tests/postgres` with the P1 suite, against `pfactory_test`;
  - the ratchet in CI form (ruff + mypy);
  - ruff 0.15.17.
