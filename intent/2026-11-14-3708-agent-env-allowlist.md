---
status: approved
issue: olafkfreund/Factory#3708
author: olafkfreund
---

# Intent: agent children get an allowlisted environment

## Problem

PFactory starts agents (the Claude SDK client, the codex, gemini and copilot CLI
runners, and the tool executor's shell) as children of the control plane, and
they inherit its whole environment. The pod carries `GITHUB_TOKEN`,
`APP_API_TOKEN`, `APP_JWT_SECRET`, the database URL and the S3 keys. The agent
reads untrusted input (raw issue text and cloned repositories), so a prompt injection can make it run `env`
and send those secrets out. AIFactory closed this in #1680 and #1757 with
`core/child_env.py` and `is_agent_env_allowed` / `is_denied_env_key`;
PFactory has no equivalent (`core/client.py` says "Caller is responsible for env
scrubbing", and no caller does it).

## Proposed outcome

- Every child that runs an agent, or runs code the agent or the repository
  under test controls, gets an allowlisted environment built by one helper.
- Such a child cannot read `APP_API_TOKEN`, `APP_JWT_SECRET`, the database
  URL, the S3 credentials or `GITHUB_TOKEN`. It keeps only the credential its
  own step needs (for example the Claude OAuth token), and the spec names each
  one with its reason.
- Tests assert this for each spawn site.

## Affected users and systems

- PFactory backend (`apps/backend/core`, `providers`, `tools/executor.py`) and
  web server (`server/services` spawn sites).
- Operators who pass an extra variable to agents on purpose: they get a
  documented opt-in variable instead of implicit inheritance.
- The same change lands in the sibling service (PFactory / TFactory) under the
  same slug.

## Constraints

- Follow AIFactory's rules and names so the three copies can later become one
  package. Building that shared `factory-sandbox` package is out of scope; a
  follow-up issue tracks it.
- Keep the diff to env and spawn code. Other teams are editing WebSocket
  routing (PF#994, TF#1500) and the run signature (PFactory#996).
- No change to what the control plane itself or its first-party runner
  (`run.py`) can read: they need the database, S3 and the API token to work.
- Claude auth must keep working: OAuth token, proxy token and base URL stay.
- The interactive PTY shell is the user's own shell and keeps its environment.

## Open questions

None. The owner pre-approved the document gates for the 2026-11-14 bulk run.
