---
status: approved
issue: 996
intent: intent/2026-11-14-996-intake-attribution.md
---

# Spec: originator and agent identity on plan sessions and in the run signature

## Design

Follow the path `org_id` already takes (#968): resolved in the route, passed to the service as a keyword, stamped on the session at intake, read at emit.

**1. Two session fields.** `PlanSession` (`apps/backend/plan/service.py`) gains `originator: str | None = None` and `agent_identity: str | None = None`, next to `org_id`. Sessions are persisted as their pydantic JSON, so there is no migration, and a payload written before this change loads with both null.

**2. Service keywords.** `PlanService.ingest_text` and `ingest_bytes` gain keyword-only `originator: str | None = None` and `agent_identity: str | None = None`, stamped on the session where `org_id` is (`value or None`). Defaults are null, so `plan/agent_api.py`, `routes/github.py` (PR review) and every existing test call are unchanged and record null.

**3. One pure helper** in `apps/web-server/server/routes/plan_pipeline.py`, next to `_user`:

`_attribution(user, *, authored_elsewhere: bool = False) -> dict[str, str | None]` returning `{"originator": ..., "agent_identity": ...}`.

- `user` not a dict: both null.
- `originator`: `user["id"]` when it is a non-empty `str`, the principal is not `is_service`, the id is not `"default"`, and `authored_elsewhere` is false. Otherwise null. Never the email.
- `agent_identity`: `f"agent:pfactory/{api_key_id}"` when `user["api_key_id"]` is a non-empty `str`. Otherwise null. Independent of `originator`.
- Values are read with `.get()` (a JWT principal has no `api_key_id`). A value that is blank after `strip()` or contains a non-printable character (`not value.isprintable()`) is null: the signature is written to `.run.json` and printed by the governance CLI.

**4. Three call sites** in the same file pass `**_attribution(_user(request))` to the service:

- `ingest_text` and `ingest_upload`: the caller is the originator.
- `ingest_from_issue`: `authored_elsewhere=True`, so `originator` is null and `agent_identity` is still recorded.

The MCP proxy routes (`mcp_stdio/router.py`) set `request.state.user` and call these same handlers, so they need no change.

**5. Run signature.** `_run_signature` in `apps/backend/plan/emit/docs/render.py` writes `session.originator` and `session.agent_identity` in place of the two `None` literals. `SIGNATURE_SCHEMA` stays 2: the fields and their null meaning already exist.

## Alternatives rejected

- **Prefix the originator (`human:pfactory/<id>`)**: AIFactory#1897 stores the raw id; the prefix is one formatting change for both services, tracked on the epic.
- **Fall back to the email when there is no id** (AIFactory does): the signature is emitted with the plan's documents; an email there is personal data in a repository.
- **Record the caller as originator on from-issue**: the originator of an issue is its author (AIFactory#1897). Recording the caller now would change the field's meaning on that lane later.
- **Add `author` to `FromIssueBody` now**: the body is `extra="forbid"` and a documented cross-repo contract; it needs AIFactory's poller changed in step. Separate issue.
- **Resolve the principal inside `PlanService`**: the service has no request; the route already does this for `org_id`.
- **A `services/originator.py` module as in AIFactory**: one fifteen-line function with one caller file does not need a module.
- **Thread a principal into `plan_review_pr` and `agent_api.plan_ingest`**: the first has the same author-versus-caller question as from-issue, the second has no principal. Both stay null.

## Risks

- **A user id in emitted documents.** It is the opaque `users.id` (JWT `sub` is minted from it). Accepted in the intent; no email or name.
- **`_session_dict` returns the new fields** to API callers through `model_dump()`. Those callers already pass `require_plan_access` for the session; the id is not a secret.
- **JWT `sub` of an unexpected type** (not a string): dropped to null rather than coerced.
- **Old sessions**: null defaults; a test loads a payload without the fields.
- No host, chart or config change. No change to `auth.py`.

## Verification

- Unit tests for `_attribution`: JWT user gives the id and no agent; `acw_` key user gives both; service principal and `"default"` give null originator; `authored_elsewhere=True` gives null originator and keeps the agent; non-dict, non-string id, empty string and a value with a line break give null; the email is never returned.
- Route tests call the handlers directly, the pattern in `tests/test_plan_from_issue_route.py` (`asyncio.run`, a stub request), with `state.user` added to the stub. No `TestClient`: the auth middleware opens its own database session, which dependency overrides do not reach. Cases: `ingest_text` with a keyed principal stores both on the session; `ingest_upload` with the same principal stores both (covers `ingest_bytes`); `ingest_from_issue` stores `agent_identity` and a null `originator`.
- Round trip: a session ingested with both keywords set, dumped with `model_dump_json` and reloaded with `PlanSession.model_validate_json`, keeps both. A payload without the fields reloads with both null.
- `tests/test_plan_docs_run_signature.py`: a session ingested with both keywords emits both in the signature; the existing all-null case still passes.
- `ruff` and `scripts/ratchet_lint.py` clean for touched files; `tests/test_governance_cli.py`, `tests/test_plan_session_org_scope.py`, `tests/test_mcp_stdio_proxy.py` pass unchanged.
