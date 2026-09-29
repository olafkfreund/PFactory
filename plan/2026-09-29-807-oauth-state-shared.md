---
status: approved
issue: 807
spec: spec/2026-09-29-807-oauth-state-shared.md
---

# Plan: email OAuth state and the GitHub device flow work, or fail honestly, with more than one replica

Self-contained summary of the approved decisions:

- **Table:** a new model `OAuthConnectState`, table `oauth_connect_states`,
  with no credential in it:
  - `state` `String(64)` PK;
  - `user_id` `String(36)`, no FK;
  - `provider` `String(16)`;
  - `origin` `String(255)`, nullable;
  - `expires_at` `DateTime(timezone=True)`, indexed.

  The migration's `down_revision` is `"e5b8c3f1a7d2"`, and downgrade drops the
  table.
- **`routes/email.py`:** `_pending_connect_states` and
  `_cleanup_expired_states` are replaced by:
  - `_save_connect_state(user_id, provider, origin) -> str`: sweeps rows with
    `expires_at <= now`, inserts the new row with `now + 600 s`, commits and
    returns the token;
  - `_consume_connect_state(state, provider) -> dict | None`: one `DELETE ...
    WHERE state AND provider AND expires_at > now RETURNING user_id, origin`,
    then commit. Single-use across pods, and bound to its provider.

  Both starts and both callbacks use them. The callbacks' failure page is
  unchanged.
- **`routes/github.py`:** a local `_replica_count()` parses
  `PFACTORY_REPLICA_COUNT` with the same rule as `plan/service.py` (a missing
  or bad value means 1).
  - With `> 1`, `/auth/start` returns
    `{"success": True, "data": {"success": False, "message": <refusal>}}`
    before spawning `gh`. The refusal says the credential would exist on one
    pod only, and to set `GITHUB_TOKEN` in the PFactory Secret.
  - `/auth/status` is unchanged, and so is the frontend (it already shows
    `data.message`).
- **Rejected:** a signed stateless token, session affinity, a shared
  replica-count module, persisting the `gh` token.

All work happens in the worktree `/tmp/.../scratchpad/pf-807` on
`fix/807-oauth-state-shared`, with the backend `.venv` symlinked in and never
staged. Postgres runs use a dedicated pg16 container with a `pfactory_test`
database, stopped afterwards.

## Steps

1. **Tests first:**
   - a new `apps/web-server/tests/test_email_oauth_state_shared.py`, using an
     in-memory SQLite with `create_all` and `async_session_factory` patched in
     `routes/email.py`. It covers:
     - a state saved in one session is consumed in a fresh one and returns
       `user_id` and `origin`;
     - a second consume returns None;
     - an expired row returns None;
     - a `gmail` state is refused by an `outlook` consume;
     - a save sweeps expired rows;
   - a new `apps/web-server/tests/test_github_auth_replicas.py`, with
     `shutil.which` and `asyncio.create_subprocess_exec` patched in
     `routes/github.py`:
     - with `PFACTORY_REPLICA_COUNT=2`, `POST /api/github/auth/start` returns
       the refusal and the subprocess is never spawned;
     - with it unset, there is no refusal;
   - a new `tests/postgres/test_oauth_state_race.py` (marked postgres and
     slow, migrated with `run_alembic`): 2 concurrent consumes of one state,
     and exactly one gets the row.

   → verify: all three fail on the current code (no helpers, no table, no
   refusal).

2. **Model and migration:** add `OAuthConnectState` to `database/models.py`,
   plus a new migration under `database/alembic/versions/`.
   → verify: on Postgres, `alembic upgrade head`, then `downgrade -1`, then
   `upgrade head`, all succeed.

3. **`routes/email.py`:** the two helpers, and the four call sites switched.
   → verify: the email tests from step 1 pass, and so do the existing
   `test_email_oauth_*` tests.

4. **`routes/github.py`:** `_replica_count()` and the early refusal.
   → verify: the device-flow tests pass.

5. **Docs:**
   - `docs/dev/environment-reference.md` gets a `PFACTORY_REPLICA_COUNT`
     entry covering:
     - its purpose: the replica ceiling, which is the scaler's max, not the
       current count;
     - what it gates: the #755 per-process guard, which errors or refuses
       with `PFACTORY_REQUIRE_SHARED_STORE=1`, and the device-flow refusal;
     - that unset means 1;
   - the "Do not raise the replica count yet" paragraph in
     `guides/shipping.md` drops #807 from its blocker list.

   → verify by grepping for "PFACTORY_REPLICA_COUNT" in the env reference.

6. **Gates:**
   - ci.yml per-process;
   - store mode on Postgres 16;
   - `tests/postgres -m postgres` with the P1 suite, against `pfactory_test`;
   - `scripts/ratchet_lint.py --base origin/dev --package apps/backend
     --package apps/web-server --package scripts` (ruff + mypy), after the
     commit;
   - `uvx ruff@0.15.17 format --check` on the changed files.

   Commit with the hook, staging named files only.
   → verify all green.

7. **PR** to `dev` with `Fixes #807`, linking the intent, spec and plan.
   Merge (merge commit) when the checks are green and the threads are
   addressed. CodeQL's Alembic "unused global" alerts are known false
   positives (see #810).

8. **Release** with the next patch (0.6.24): the CHANGELOG, versions,
   `package-lock.json`, `validate-release.js`, the `dev -> main` sync and the
   deploy. It can ship together with other fixes, such as #808, if they are
   ready.
   → verify in production:
   - `alembic_version` is the new head;
   - the `oauth_connect_states` table exists and is empty;
   - with one replica (`PFACTORY_REPLICA_COUNT` unset), `POST
     /api/github/auth/start` is not refused. Not exercised end to end:
     production does not use the device flow.

## Tests

```bash
cd <pf-807 worktree>
export PATH=/mnt/data/Source-home/GitHub/PFactory/apps/backend/.venv/bin:$PATH
U=postgresql+asyncpg://postgres:pw@localhost:<port>/pfactory_test
pytest apps/web-server/tests/test_email_oauth_state_shared.py apps/web-server/tests/test_github_auth_replicas.py -q
TEST_POSTGRES_URL=$U pytest tests/postgres/test_oauth_state_race.py -m postgres -q
pytest tests/ apps/web-server/tests/ -m "not slow" -q
DATABASE_URL=$U pytest tests/ -m "not slow and not postgres" -q
TEST_POSTGRES_URL=$U pytest tests/postgres -m postgres -q
python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
```

## Rollback

- Revert the fix commit and release a patch.
- The old code ignores the table, so it can stay. `alembic downgrade -1`
  drops it if wanted.
- An email connect started just before the rollback has to be retried.
