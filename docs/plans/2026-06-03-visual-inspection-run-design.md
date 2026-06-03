# Visual Inspection Run — design

> Status: Design (approved 2026-06-03). Implementation is phased + issue-decomposable.
> Builds on the evidence subsystem, visual baselines (#109), test-target auth
> (#107), the SaaS connector (#111), and mirrors the cloud-assessment pattern
> (remediation plan · downloads · GitHub export · Cloud Reports portal).

## Summary

A **Visual Inspection Run** generates and runs a Playwright **browser** test
against a *visual target* (e.g. a ServiceNow UI, not just its Table API),
**records** it (Playwright trace.zip + video + step-labeled screenshots),
captures **verification** screenshots and **error/problem** screenshots, and
packages everything into a committed repo folder:

```
automated-test/<YYYY-MM-DD-HHMMSS>/
```

containing the screenshots, the recording, a human-readable **visual-inspection
report** (Markdown, per-step, with annotated errors), an LLM **correction plan**
(AiFactory-task-ready), a **GitHub issue export** (dry-run), and `meta.json`.
The folder is committed to the system-under-test (SUT) repo on a branch
(dry-run by default) and surfaced in the portal as **Visual Reports**.

The goal is **human visual inspection + tracking**: a reviewer sees what passed,
what broke (with screenshots), why, and a concrete plan to fix it.

### Decisions (from the brainstorm)

1. **"Record" = capture the generated run.** PFactory generates + runs the test
   (today's model); the *run* is recorded (trace + video + screenshots). No
   human codegen step.
2. **`automated-test/<datetime>/` lives in the SUT repo**, committed to a branch,
   **dry-run by default** (mirrors the Triager `git_writer` opt-in).
3. **Hybrid report generation** — deterministic assembly for the report;
   **LLM only** for the recommendations + correction plan.
4. **Browser/visual is a lane/mode on the existing target** — reuse
   `ConnectorTarget`/`HttpTarget` + the credential vault, not a new target type.
5. **Trigger = a `+Task → Visual Inspection` template** (mirrors
   `+Task → Cloud Infrastructure`), plus a handover opt-in (see §Handover).
6. **Per-step screenshots = test-emitted + labeled** (`page.screenshot({ path:
   'NN-<step>.png' })`), deterministic and named for the report.

### Non-goals

- Human-recorded Playwright codegen authoring (rejected in the brainstorm).
- A new standalone target type (reuse the existing one).
- SAST/DAST of the SUT (out of scope, as ever).

## Architecture

A new backend package `apps/backend/agents/visual_inspection/`, mirroring
`agents/cloud/`. It composes existing primitives; it does not re-implement
capture, auth, or rendering.

| Module | Role | Reuses / mirrors |
|--------|------|------------------|
| `packager.py` | `package_run(spec_dir, target, results) -> RunDir` — collect per-step + error screenshots, video, trace into the `automated-test/<datetime>/` tree | `agents/evidence/layout` capture |
| `report.py` | **Deterministic** `render_inspection_report(...)` — per-step Markdown table with screenshot thumbnails, pass/fail, annotated errors | cloud `report.py` |
| `correction_plan.py` | **LLM** `render_correction_plan(...)` — recommendations + AiFactory-task-ready plan from the failures + error shots + trace summary; mockable SDK seam; deterministic fallback | evaluator LLM seam |
| `issues.py` | `build_issue_specs(...)` — epic + one child per failed step → `register_issues` (GitHub, **dry-run default**) | cloud `issues.py` |
| `store.py` | `~/.pfactory/visual-inspections/<id>/` — list/read/download (md/pdf via pandoc+chrome) | cloud `store.py` |
| repo commit | commit `automated-test/<datetime>/` to the SUT repo on a branch (**dry-run default**, env opt-in `PFACTORY_VISUAL_GIT_WRITE=1`) | `tools/git_writer.py` (extend) |

> `tools/git_writer.write_tests_to_branch` is scoped to *test files*. Committing
> an arbitrary artifact folder (report.md, pdf, video, trace.zip) is broader, so
> P4 adds a generalized `write_paths_to_branch(paths, branch, *, dry_run=True)`
> helper next to it (same dry-run-first contract), rather than assuming a
> drop-in reuse.

### The `automated-test/<datetime>/` folder

```
automated-test/2026-06-03-130500/
  report.md              # human visual-inspection report
  report.pdf             # rendered on demand (store.py)
  correction-plan.md     # LLM correction plan (AiFactory-task-ready)
  issues.json            # GitHub issue specs (dry-run)
  meta.json              # target, timestamp, per-step + overall verdict, counts
  screenshots/
    01-login-pass.png
    02-open-incident-pass.png
    03-submit-fail.png   # error / problem screenshot (annotated in report.md)
  recording/
    video.webm
    trace.zip            # `npx playwright show-trace` replayable
```

`meta.json` is the machine-readable contract between the P1 writer and the P4
portal reader (+ the report renderer) — pin it to avoid drift:

```json
{
  "id": "snow-acme-20260603130500",
  "target": { "name": "snow", "platform": "servicenow", "base_url": "https://acme.service-now.com" },
  "created_at": "2026-06-03T13:05:00Z",
  "verdict": "fail",                       // pass | attention | fail
  "counts": { "steps": 3, "passed": 2, "failed": 1 },
  "steps": [
    { "n": 1, "label": "login", "state": "pass", "screenshot": "screenshots/01-login-pass.png" },
    { "n": 3, "label": "submit", "state": "fail", "screenshot": "screenshots/03-submit-fail.png",
      "error": "expected toast 'Saved' — got 'Required field'" }
  ],
  "recording": { "video": "recording/video.webm", "trace": "recording/trace.zip" }
}
```

## Target model — the visual lane

Add a **browser/visual lane** to the existing target rather than a new type:

- `ConnectorTarget` / `HttpTarget` gains an opt-in marker (e.g.
  `lanes: [browser]` or `visual: true`) declaring the target should also be
  driven via the **UI**. Auth + `base_url` are unchanged (`auth: { type: ref }`
  resolves SSO/OAuth from the vault, reusing #107).
- For ServiceNow, this enables browser-lane generation against the ServiceNow
  UI with a storageState SSO login, alongside the existing API-first connector.

> Note: this means a connector now supports **two lanes with different stability
> profiles** — the API lane (stable, preferred) and the new opt-in visual/browser
> lane (SSO/iframe-fragile). That is intentional, not a contradiction of the
> connector's API-first default; the visual lane is for genuinely UI-only
> inspection. Update the `ConnectorTarget` docstring to say so when P3 lands.

```yaml
targets:
  - name: snow
    type: connector
    platform: servicenow
    base_url: https://acme.service-now.com
    visual: true                  # NEW — also drive the UI (browser lane)
    auth:
      type: ref
      ref: snow-svc
      login_url: https://acme.service-now.com/login.do
      # selectors / success_url_pattern → auth.setup.ts (storageState)
```

## Per-step screenshots

Gen-Functional's **browser template** (for visual targets) emits a labeled
screenshot at each verification step:

```ts
await page.screenshot({ path: `${stepNo}-${slug(stepLabel)}-pass.png` });
```

and on assertion failure the existing `screenshot: 'only-on-failure'` policy
captures the `*-fail.png` error shot. The naming convention (`NN-<label>-<state>`)
lets `packager.py` assemble the report in step order, deterministically.

## Data flow

```
+Task → Visual Inspection  (or handover with visual_inspection enabled)
   │
   ▼  backend run route  POST /api/visual-inspection/run
   │
   ▼  GATE: storageState login to the target (creds via the vault, #107)
   │        — "do we get in?"  fail → stop, report no-access (mirrors cloud gate)
   ▼
   Gen-Functional → browser test(s) with step-labeled screenshots
   ▼
   Executor → Playwright run (screenshots · video · trace captured)
   ▼
   packager → assemble automated-test/<datetime>/
   ▼
   report.py (deterministic)  +  correction_plan.py (LLM)  +  issues.py (dry-run)
   ▼
   store + optional SUT-repo commit (git_writer, dry-run default)
   ▼
   portal → Visual Reports (list → detail: report · gallery · video · plan · downloads)
```

## Handover integration (`/handover-to-pfactory`)

The handover skill (`.claude/skills/handover-to-pfactory` + the
`companion-skills/aifactory-handover-to-pfactory` that ships to AIFactory repos)
currently snapshots the spec/diff and calls
`mcp__pfactory__task_create_and_run`. Extend it to **interactively ask, at
handover**:

1. **What to do** — the task description / acceptance focus (already partly
   prompted; make it explicit).
2. **Enable visual inspection?** — yes/no; if yes, which **visual target** (the
   platform/url + auth ref) and the **flow/criteria** to inspect.

The answers thread a `visual_inspection: { enabled, target, flow }` block into
`task_create_and_run`'s metadata → written into the task's `.pfactory.yml` /
`task_metadata.json`. The pipeline reads it: when enabled, the browser lane runs
against the visual target and the packager produces the `automated-test/<datetime>/`
artifact. When disabled, behaviour is unchanged.

Seam: `apps/backend/agents/tools_pkg/tools/task_control.py`
(`task_create_and_run`) gains an optional `visual_inspection` param threaded to
the snapshotter + metadata; the MCP tool schema (`mcp_server/pfactory_server.py`)
exposes it; the two handover skills prompt for it.

## Verdict + error handling

- **Verdict** (mirrors cloud): per-step pass/fail → overall
  `pass` / `attention` / `fail`. Error screenshots *are* the tracked problems.
- Each failed step → a report annotation + a GitHub **child issue** (what
  broke · screenshot · recommendation · fix) + a correction-plan entry.
- **Best-effort tail:** the LLM correction plan, the GitHub export, the PDF
  render, and the repo commit never break the run — the deterministic
  `report.md` + screenshots + recording always land. The login gate failing
  stops the run cleanly with a no-access report (no partial artifacts).

## Testing strategy

Unit-testable without a live tenant (the bulk):
- `packager.py` — assembles the folder from canned evidence (screenshots/video/
  trace stubs); correct structure + `meta.json`.
- `report.py` — deterministic Markdown from canned per-step results (byte-stable;
  thumbnails wired; errors annotated).
- `correction_plan.py` — injected LLM seam (no real SDK); deterministic fallback.
- `issues.py` — epic + child-per-failure; dry-run makes no calls.
- run route — mocked gate + run + packager (mirrors the cloud route tests).
- handover — `task_create_and_run` threads the `visual_inspection` block.

**Needs a live ServiceNow tenant** (flagged, not fabricated):
- the SSO storageState login actually authenticating,
- the browser test driving the real (iframe/dynamic) ServiceNow UI,
- the end-to-end `automated-test/<datetime>/` against a real flow.

## Phasing (issue-decomposable)

- **P1 — Run packaging + deterministic report.** `agents/visual_inspection/`
  (`packager` + `report` + `meta`), the `automated-test/<datetime>/` structure,
  the step-labeled screenshot convention in the browser template. *(No tenant.)*
- **P2 — Correction plan + GitHub export + downloads.** `correction_plan.py`
  (LLM seam) + `issues.py` + md/pdf via `store.py`. *(No tenant.)*
- **P3 — ServiceNow browser/visual lane + SSO.** the `visual` marker on the
  target, redirect-aware storageState SSO, ServiceNow selector/iframe guidance
  in the `context_block`. *(⚠️ needs a live tenant.)*
- **P4 — Portal + run route.** `+Task → Visual Inspection` launcher (mirror
  `CloudCheckDialog`), `POST /api/visual-inspection/run`, Visual Reports page
  (mirror Cloud Reports), SUT-repo commit (`git_writer`).
- **P5 — Handover integration.** `task_create_and_run` + MCP schema + the two
  handover skills prompt for "what to do" + "enable visual inspection?".

## Risks

- **ServiceNow SSO** is often SAML/OIDC *redirect*, not a form POST — the
  form-login `auth.setup.ts` may need a redirect-aware variant. Validate on a
  live tenant.
- **ServiceNow dynamic DOM** — iframes (`gsft_main`), dynamic element ids.
  `context_block` must steer generation to stable selectors (aria/label/data-*)
  and iframe handling. Validate on a live tenant.
- **Repo commit safety** — writing `automated-test/` into the SUT repo must be
  dry-run by default and never push (consistent with the no-automatic-pushes
  policy); the user opts in per the Triager pattern.

## Backwards compatibility

Purely **additive**: a new opt-in target marker, a new backend package, new
portal page + route, and an optional handover prompt. Existing browser-lane
behaviour and all current targets are unchanged; the step-screenshot convention
applies only to visual targets.
