# /demo scenario catalog

The `/demo` command routes on a **scenario id**. Each scenario is a known-good
user story with a fixed spec + acceptance criteria, so every recording tells the
same coherent story and the [quality gate](../../../scripts/demo/quality-gate.sh)
can assert the expected verdict mix. Pass the id as the skill argument
(`/demo greeting-generator`). Default when none given: `greeting-generator`.

**All four are wired.** One command seeds any of them — it writes the simulated
AIFactory spec workspace and (for local SUTs) materialises the finished feature
into a real git repo with a base→branch diff, a `.pfactory.yml`, and an empty
`tests-catalog.json`, then prints the handover parameters:

```bash
scripts/demo/seed-scenario.sh <scenario>        # python-unit | multi-lane | greeting-generator | failure-flow
scripts/demo/seed-scenario.sh --list
```

Scenario assets live under `tests/fixtures/demo-scenarios/<id>/` (`meta.env` +
`spec-content/` + `sut/`). `greeting-generator` delegates its spec seed to the
existing `scripts/seed-aifactory-workspace.sh` (its SUT is the external repo).

| id | lane(s) | SUT | seeded failure? | wired today |
|---|---|---|---|---|
| `greeting-generator` | browser (Playwright) | `olafkfreund/pfactory-demo` Vite/React app | yes — AC#5 | ✅ fixtures + seed script |
| `python-unit` | unit (pytest) + mutation | `pricing.py` helper module | yes — under-asserted rounding → mutation flag | ✅ `tests/fixtures/demo-scenarios/python-unit/` |
| `multi-lane` | unit + browser + api | polyglot `/quote` (py + React + httpx) | no | ✅ `tests/fixtures/demo-scenarios/multi-lane/` |
| `failure-flow` | inherits base scenario | (lens over `greeting-generator`) | yes | ✅ narrative overlay (`--base` selectable) |

---

## `greeting-generator` — browser showcase (the flagship)

**Status:** fully wired. Seed with
[`scripts/seed-aifactory-workspace.sh`](../../../scripts/seed-aifactory-workspace.sh);
canonical spec lives in `tests/fixtures/pfactory-demo/spec-content/`.

- **SUT:** public Vite + React + TS app at
  `https://olafkfreund.github.io/pfactory-demo/`. Two dropdowns
  (`category`, `tone`), Generate + Clear buttons, `[data-testid=output]`.
- **Story:** a visitor picks category + tone, clicks Generate, sees matching
  text; Clear empties it; Generate again should give a *fresh* result.
- **Acceptance criteria + expected verdicts:**

  | AC | What | Verdict |
  |---|---|---|
  | AC#1 | Generate produces non-empty `[data-testid=output]` | accept |
  | AC#2 | `category=greeting` → output ∈ {hello, hi, greetings, welcome} | accept |
  | AC#3 | `tone=snarky` → output ∈ {obviously, whatever, sure, fine} | accept |
  | AC#4 | Clear empties `[data-testid=output]` | accept |
  | AC#5 | Two consecutive Generate clicks → **different** text | **reject** (seeded memoisation bug) |

- **`EXPECT_FAILURE=1`** — the quality gate asserts the report shows both
  accept and reject, so the "pass AND fail" beat is always in frame.
- **Seed:** `scripts/seed-aifactory-workspace.sh`
- **Reference:** `docs/plans/2026-05-29-pfactory-demo-showcase-design.md`

---

## `python-unit` — pytest lane + mutation signals

**Status:** fully wired —
`tests/fixtures/demo-scenarios/python-unit/`. Seed with
`scripts/demo/seed-scenario.sh python-unit` (materialises `pricing.py` into a
git repo + AIFactory spec). Emphasises the *numeric* signals a browser demo
can't show: coverage delta, 3× stability, mutate-and-check.

- **SUT:** `pricing.py` — `apply_discount(price, pct)` and
  `bulk_total(items)`; one path (`bulk_total`'s rounding) is under-asserted so
  mutation can surface a SURVIVED mutant the dev tightens.
- **Story:** a dev finishes a pricing helper and hands it to PFactory to prove
  the maths is covered, stable, and mutation-resistant before merge.
- **Acceptance criteria + expected verdicts:**

  | AC | What | Verdict |
  |---|---|---|
  | AC#1 | `apply_discount(100, 10) == 90` and clamps pct to [0,100] | accept |
  | AC#2 | `bulk_total` sums line items with per-line discount | accept |
  | AC#3 | `bulk_total` rounds to 2 dp (HALF_UP) | **flag** — mutation SURVIVED on the rounding assertion |

- **`EXPECT_FAILURE=1`** (the flag verdict is the teaching moment).
- **Recording emphasis:** the terminal pane lingers on the Evaluator's
  coverage_delta + mutation lines; the portal pane shows the mutation lane.

---

## `multi-lane` — the polyglot Planner

**Status:** fully wired —
`tests/fixtures/demo-scenarios/multi-lane/`. Seed with
`scripts/demo/seed-scenario.sh multi-lane` (materialises `quote.py` +
`QuoteForm.tsx` + `api_quote.py` into a git repo + AIFactory spec, with a `web`
http target in `.pfactory.yml`). Showcases the v0.2 5-lane spine — one spec
produces pytest + Jest + Playwright + httpx subtasks.

- **SUT:** a `/quote` micro-feature: a Python `quote.py` calculator, a React
  `<QuoteForm>`, and an httpx-tested `GET /api/quote` route.
- **Story:** a full-stack feature lands across three files; the dev hands the
  whole branch over and watches one plan fan out across lanes.
- **Acceptance criteria + expected verdicts:** all `accept`.

  | AC | Lane | Verdict |
  |---|---|---|
  | AC#1 | unit/pytest — `quote.py` core maths | accept |
  | AC#2 | unit/jest — `<QuoteForm>` validates input | accept |
  | AC#3 | browser/playwright — submit flow renders the quote | accept |
  | AC#4 | api/httpx — `GET /api/quote?…` returns the right JSON | accept |

- **`EXPECT_FAILURE=0`** — this scenario sells *breadth*, not the failure beat.
- **Recording emphasis:** the portal LaneStatusGrid lighting up multiple lanes
  in parallel is the hero shot.

---

## `failure-flow` — the human-in-the-loop close-out (a lens)

**Status:** narrative overlay, not a separate SUT. Runs the `greeting-generator`
base (which already seeds AC#5's failure) but the **recording + narration**
focus on the *decision*: PFactory flags the failing test, the dev reviews it in
the portal, then **merges the accepted tests and dismisses the rejected run**.

- **Base:** `greeting-generator` (override with `--base <scenario>`).
- **`EXPECT_FAILURE=1`** (the base must surface a reject for the flow to exist).
- **Recording emphasis:**
  1. terminal: handover + the Triager printing `reject` on AC#5
  2. portal: open the triage report, the human reviewing the failing verdict
  3. portal: **merge** the 4 accepted tests to the branch
     (`PFACTORY_TRIAGER_GIT_WRITE=1`), then **dismiss/discard** the run
  4. report pane: the final triage_report.md with the merge + dismissal recorded
- This is the only scenario where the demo deliberately opts the Triager out of
  dry-run (`PFACTORY_TRIAGER_GIT_WRITE=1`) so the merge is real on the demo
  branch. Never enable PR-comment side-effects for a demo.
