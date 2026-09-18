---
status: approved
issue: 671
intent: intent/2026-09-18-671-process-reject-unknown-fields.md
---

# Spec: `/process` silently drops `repo`/`base_ref`

## Design

In `apps/web-server/server/routes/plan_pipeline.py`, add one base class and
have every JSON request body inherit it:

    class _StrictBody(BaseModel):
        # Unknown fields are a 422, not a silent no-op (#671).
        model_config = ConfigDict(extra="forbid")

Applied to (**decided, intent Q1: all of them**): `IngestTextBody`,
`FromIssueBody`, `ApproveBody`, `RejectBody`, `DiscardBody`, `WaiveBody`,
`ApproveAccessBody`, `EmitBody`, `EmitContractBody`, `PlanUpdateBody`,
`AcceptedSuggestion`, `ApplySuggestionsBody`. FastAPI already renders the
Pydantic `extra_forbidden` error as a 422 naming the offending field, so no
handler code changes. `/ingest` (multipart form) is not a Pydantic body and is
unaffected.

A bare `POST /process` with no body still works: `updates` is
`PlanUpdateBody | None = None`.

### Client audit (done while writing this spec)

| client | route(s) | keys sent | ok? |
| --- | --- | --- | --- |
| `apps/frontend-web/src/lib/planning-api.ts` + stores | ingest-text, process, approve, reject, emit, waive, suggestions/apply | match TS types, which match the models | yes |
| `agents/tools_pkg/tools/plan_control.py` (MCP) | ingest-text, process (`{}`), approve | text/title/category/template; approver/feedback | yes |
| `plan/access_cli.py` | access/approve | resource/approved_by/scope/approved_at | yes |
| `.github/workflows/pfactory-task.yml` | ingest-text | title/text/channel | yes |
| `.claude/skills/handover-to-pfactory` | emit, emit-contract | repo/dry_run | yes |
| CFactory `card_intake.py`, `actions.py` | ingest-text, approve, reject, process | title/category/channel/text; approver/feedback; `{}` | yes |
| AIFactory `intake_poller.py` | from-issue | repo/provider/issue_number/title/body/labels/autonomy_tier/change_mode | yes |
| **`~/.claude/skills/parr-run/SKILL.md:117`** | approve | `approver`, **`auto_restart:false`** | **no — would 422** |

`auto_restart` is AIFactory's approve parameter; PFactory never read it. The
fix is to delete `auto_restart:false` from that one line of the `parr-run`
skill. That file is the user's global skill, outside this repo, so it is edited
in the same work session but not in this PR's diff.

## Alternatives rejected

- **`PlanUpdateBody` only**: the other 11 bodies have the same silent-drop
  hazard (e.g. `emit-contract` with a misspelt `project-id` silently emits
  without a project).
- **Accept `repo` on `/process` and re-run reconnaissance**: larger, and
  duplicates `/ingest-text`'s job; the intent chose the 422.
- **Exempt `ApproveBody` to keep `parr-run` working**: leaves a hole exactly
  where an automated conductor calls; the right fix is the one-line skill edit.

## Risks

- Unknown out-of-tree callers sending extra keys start getting 422. Mitigation:
  the error names the field, so the break is loud and one-line to fix — the
  opposite of today's silent wrong answer. Called out in the PR description.
- Release note needed (behaviour change on a public API).

## Verification

- Test: `POST /process` with `{"repo": "o/r", "base_ref": "main"}` → 422 and
  the detail names `repo`; the session is not processed.
- Test: bare `POST /process` and `{"title": "x"}` still 200 (existing tests).
- Test: one unknown key on `emit-contract` → 422 (covers the base class on a
  second route).
- Frontend unit tests unchanged and green (`npm test` in `apps/frontend-web`
  for `planning-api.test.ts`).
- `apps/backend/.venv/bin/pytest tests -q -k "plan_pipeline or plan_route or plan_session"`
  green.
