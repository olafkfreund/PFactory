---
status: approved
issue: review-fixes (Factory#1016, #1015, #1012, #1004)
intent: intent/2026-11-16-review-fixes-pfactory.md
---

# Spec: PFactory cross-PR review fixes

## Design

1. PTY env: `pty/session.py start()` builds env from `os.environ` minus `RUNNER_STRIP` (from `server/utils/subprocess_env.py`), case-insensitive as `make_subprocess_env` does. Not `make_subprocess_env` itself: it also strips `ANTHROPIC_API_KEY`, and the user's shell is the documented exception.
2. Tenant: `_tenant()` in `routes/contract_events.py`. The ingress header is authoritative. Header and body both present and different: 403. Header only or both equal: header. Body only (internal emitter, no header): body. Neither: `resolve_tenant` default. Residual: a caller that reaches the service bypassing the ingress and omits the header can still name a tenant; that path is not user-reachable.
3. Memory caps: `memory.retention.AGENT_SESSION_TYPES` maps coder to build, qa_reviewer and qa_fixer to verification, insights to insights. `create_magestic_ai_mcp_server(..., agent_type)` passes it to `create_memory_tools(..., memory_session_type)`, used by `record_gotcha`. `post_session_processing` passes `session_type="build"` through `save_session_memory` to `save_session_insights`. Unmapped agents keep the env/default behaviour.
4. Project env: keep the endpoint keys allowlisted (the settings UI writes them) but `load_project_env_file` ignores an endpoint key when any paired credential (`_ENDPOINT_CREDENTIALS`) is already in the host env, snapshotted before the file is read so line order does not matter.

## Alternatives rejected

- Remove the URL keys from the allowlist: breaks the settings UI and `test_every_settings_route_key_is_allowed`; the repo setting is legitimate when no host credential exists.
- Tenant: body always wins (today) is the bug; header always wins breaks internal emitters that send no header.
- Memory: set `MEMORY_SESSION_TYPE` in the process env: global mutable state, wrong when agent types share a process.

## Risks

- A deployment that relied on a repo `.env` overriding an endpoint while the host holds the credential stops getting the override (intended).
- A user's terminal loses the stripped vars; nothing legitimate reads them from a shell.
- Emitters that send a header naming a different tenant than the body get 403 (intended).

## Verification

New tests, each failing on current `dev`: `tests/test_terminal_ownership.py`, `tests/test_contract_events_feedback.py`, `tests/test_memory_retention.py`, `apps/web-server/tests/test_project_env_allowlist.py`. CI command: `pytest tests/ apps/web-server/tests/ -m "not slow"` plus the diff ratchet lint.
