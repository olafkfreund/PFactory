---
status: draft
issue: 807
author: Olaf Krasicki-Freund
---

# Intent: email OAuth state and the GitHub device flow work, or fail honestly, with more than one replica

Blocks factory-gitops#273 (lifting the PFactory KEDA one-replica pin).

## Problem

Two interactive auth flows keep their state in the memory of the pod that
started them.

1. **Email OAuth connect (Outlook, Gmail).** `routes/email.py:114`
   `_pending_connect_states` maps a random `state` token to `{user_id,
   provider, created_at, origin}`:
   - `/auth/{outlook,gmail}/start` writes it (`:287`, `:502`);
   - the provider's redirect to `/auth/{provider}/callback` pops it
     (`:339`, `:556`). The pop is single-use, and entries last 10 minutes.

   With 2 replicas, the callback lands on the other pod half the time, and
   the user gets "Invalid or expired OAuth state". The state also carries the
   `user_id` the new `EmailAccount` is bound to, because the callback request
   comes from the provider redirect.
2. **GitHub CLI device flow.** `routes/github.py:774-775` holds the
   `gh auth login` subprocess and its result in module globals. The UI polls
   `GET /github/auth/status` (`:940`), which a different pod answers with
   "not complete". Worse, `gh` writes the credential under the HOME of the
   pod that ran it (`~/.config`, an emptyDir in production). So even when it
   succeeds, it authenticates one pod, until that pod restarts.

**Production use today:** 0 `email_accounts` rows. `gh` in the pod
authenticates through `GITHUB_TOKEN` from the Secret, not the device flow. So
nothing breaks today, but both flows would break silently, or half-work, the
moment the pin is lifted.

## Proposed outcome

- An email OAuth connect succeeds whichever pod the callback reaches. The
  state stays single-use, expires in 10 minutes, and still binds the
  `user_id` and opener origin.
- The GitHub device flow never reports success for credentials that only one
  pod has. With more than one replica, it refuses up front with a clear
  message that points to the supported path (`GITHUB_TOKEN` in the Secret).
  With one replica, it behaves exactly as today.
- Both are covered by tests that simulate the second pod.

## Affected users and systems

- `apps/web-server/server/routes/email.py` (both providers' start and
  callback).
- `apps/web-server/server/routes/github.py` (`/auth/start`, `/auth/status`).
- The frontend `GitHubOAuthFlow.tsx`, which must show the refusal message.
- Possibly an Alembic migration (a state table).
- Operators connecting a mailbox, or authenticating `gh` from the portal.

## Constraints

- The state never holds a credential. Tokens still go straight into the
  `EmailAccount` row.
- It keeps CSRF protection: a state is unguessable, bound to the starting
  user, single-use, and expires.
- No behaviour change with one replica, or with `DATABASE_URL` unset (SQLite
  dev).
- Replica awareness uses the existing `PFACTORY_REPLICA_COUNT` signal (#764).
  factory-gitops#273 sets it to the scaler ceiling.

## Open questions

1. **Where the email OAuth state lives:**
   - (a) a small table (`oauth_connect_states`: state PK, user_id, provider,
     origin, expires_at), consumed with `DELETE ... RETURNING` so it stays
     single-use across pods;
   - (b) a stateless signed token (HMAC with the shared JWT secret) carrying
     the same fields plus an expiry.

   **Recommended: (a).** It keeps single-use exactly, and a replayed state is
   rejected. (b) needs no migration, but a state stays replayable until it
   expires, which weakens the CSRF guarantee the current code has.
2. **The device flow with more than one replica:**
   - (a) refuse with a clear message;
   - (b) make it work by persisting the resulting `gh` token into the shared
     Secret or the database.

   **Recommended: (a).** Nobody uses it in production. (b) means the portal
   writes a GitHub credential into shared storage, a security change far
   beyond this issue.
