---
status: approved
issue: review-fixes (Factory#1016, #1015, #1012, #1004)
author: olafkfreund
---

# Intent: PFactory cross-PR review fixes

## Problem

A review of today's merged PFactory PRs found four defects on `dev`:

1. #1016: the terminal PTY inherits the server's full `os.environ`, so any project member with terminal access can read `JWT_SECRET`, `APP_DATABASE_URL`, `CFACTORY_READ_KEY` and the rest of `RUNNER_STRIP`.
2. #1015: in multi-tenant mode `/api/contract-events` takes the tenant from the body in preference to the ingress `X-Tenant-Id`, so a caller can write into, and inject prompt text for, another tenant's plan session.
3. #1012: per-session-type memory caps are never wired; every session resolves to the `planning` cap of 5, so build sessions lose memory beyond 5 items.
4. #1004: the repo-controlled project `.env` allowlist admits endpoint keys (`OPENAI_COMPATIBLE_BASE_URL`, `GIT_BASE_URL`, ...) that redirect host-held credentials to an attacker host.

## Proposed outcome

- The terminal shell env never contains `RUNNER_STRIP` keys.
- A request whose body tenant differs from its `X-Tenant-Id` header is refused (403).
- Coder sessions save and record gotchas under the `build` cap (QA: `verification`, insights: `insights`).
- A project `.env` cannot set an endpoint key when the host already holds the matching credential.
- Each fix has a regression test that fails on current `dev`.

## Affected users and systems

PFactory only: `apps/web-server/server/pty/session.py`, `routes/contract_events.py`, `utils/project_env.py`, `apps/backend` memory and tool registry.

## Constraints

- Smallest focused change per defect; no new transport or contract fields.
- Internal emitters that send no `X-Tenant-Id` header keep working (tenant from body).
- The settings UI keeps writing endpoint keys to the project `.env` (`routes/context.py`); they must still load when no host credential exists.
- The PTY keeps the rest of the user's normal environment.

## Open questions

None. Out of scope: confining a terminal shell's filesystem reach beyond its starting cwd (#1016), a separate hardening issue.
