---
layout: default
title: Progress
permalink: /progress/
nav_order: 8
---

# v0.2 progress

> Live snapshot of the v0.2 task delivery progress. Numbers update
> by hand as merges land — last refresh on 2026-05-29.
>
> v0.1.0-mvp shipped 12 of 12 tasks on 2026-05-28 (the walking skeleton
> for Python+pytest); see the
> [v0.1.0-mvp release](https://github.com/olafkfreund/PFactory/releases/tag/v0.1.0-mvp)
> and the v0.1 entry in the
> [changelog](https://github.com/olafkfreund/PFactory/blob/main/CHANGELOG.md).

## At-a-glance

```
v0.2.0 — Enterprise Test Framework Spine (released 2026-05-29)
  ████████████████████████████  16 of 16 tasks shipped + Triager follow-up

  Done:        #16  #17  #18  #19  #20  #21  #22  #23
               #24  #25  #26  #27  #28  #29  #30  #31  #32
               + Triager evidence-links (5d8f588)
  Release:     https://github.com/olafkfreund/PFactory/releases/tag/v0.2.0

Driver doc:    docs/plans/2026-05-28-enterprise-test-frameworks-design.md
Task plan:     docs/plans/2026-05-28-enterprise-test-frameworks-tasks.md
```

## Shipped — 16 of 16 tasks

| # | Task | Merge | Tests added |
|---|---|---|---|
| [#16](https://github.com/olafkfreund/PFactory/issues/16) | Task 0: Lane spine rename (functional / sast / dast / fuzz / mutation → unit / browser / api / integration / mutation) | `1ae97f9` + 4 prior commits | +25 alias coverage |
| [#17](https://github.com/olafkfreund/PFactory/issues/17) | Task 1: Framework descriptor registry (pytest + Jest + Playwright at MVP) | [`1c92280`](https://github.com/olafkfreund/PFactory/commit/1c92280) (7 commits) | +45 |
| [#18](https://github.com/olafkfreund/PFactory/issues/18) | Task 2: `.pfactory.yml` schema + parser + validator (http / k8s / docker_compose / feature_flag targets) | [`6359590`](https://github.com/olafkfreund/PFactory/commit/6359590) | +69 |
| [#19](https://github.com/olafkfreund/PFactory/issues/19) | Task 3: `.pfactory/tests-catalog.json` schema + 3-step AC-match lookup | [`51fcebd`](https://github.com/olafkfreund/PFactory/commit/51fcebd) (6 commits) | +50 |
| [#20](https://github.com/olafkfreund/PFactory/issues/20) | Task 4: Snapshotter extension — lifts `.pfactory.yml` + tests-catalog into workspace context | [`7b2fc8b`](https://github.com/olafkfreund/PFactory/commit/7b2fc8b) (4 commits) | +9 |
| [#21](https://github.com/olafkfreund/PFactory/issues/21) | Task 5: Planner per-subtask polyglot (language / framework / target_name / intent) | [`43e9ac1`](https://github.com/olafkfreund/PFactory/commit/43e9ac1) (6 commits) | +52 |
| [#22](https://github.com/olafkfreund/PFactory/issues/22) | Task 6: Gen-Functional generic — descriptor.context_block injection; v0.1 prompt preserved as legacy | [`4c006fb`](https://github.com/olafkfreund/PFactory/commit/4c006fb) (6 commits) | +25 |
| [#23](https://github.com/olafkfreund/PFactory/issues/23) | Task 7: Per-framework Docker runner images (pytest / jest / playwright) | [`f3f88dd`](https://github.com/olafkfreund/PFactory/commit/f3f88dd) (5 commits) | +6 (skipped when no daemon) |
| [#24](https://github.com/olafkfreund/PFactory/issues/24) | Task 8: Browser-lane AppRuntime — docker-compose lifecycle + HTTP health-poll | [`d2aa2ae`](https://github.com/olafkfreund/PFactory/commit/d2aa2ae) (6 commits) | +25 |
| [#25](https://github.com/olafkfreund/PFactory/issues/25) | Task 9: TypeScript Evaluator primitives (tsc / ESLint / Stryker) | [`fde90ef`](https://github.com/olafkfreund/PFactory/commit/fde90ef) (6 commits) | +100 |
| [#26](https://github.com/olafkfreund/PFactory/issues/26) | Task 10: Evaluator coverage adapter — null-not-zero for Browser lane (Decision 11) | [`4ef9a51`](https://github.com/olafkfreund/PFactory/commit/4ef9a51) (4 commits) | +21 |
| [#28](https://github.com/olafkfreund/PFactory/issues/28) | Task 12: 15 starter test templates (5 each for Playwright / Jest / pytest) | [`e58adfd`](https://github.com/olafkfreund/PFactory/commit/e58adfd) (5 commits) | +48 |
| [#29](https://github.com/olafkfreund/PFactory/issues/29) | Task 13: Skills + slash commands (`/pfactory-init` · `/pfactory-add-test` · `/pfactory-from-template` + handover update) | [`782259f`](https://github.com/olafkfreund/PFactory/commit/782259f) (5 commits) | +36 |
| [#30](https://github.com/olafkfreund/PFactory/issues/30) | Task 14: Portal endpoints — frameworks / templates / skills / catalog | [`94e711b`](https://github.com/olafkfreund/PFactory/commit/94e711b) (6 commits) | +35 effective (43 declared; 8 hit the pre-existing starlette shim path) |
| [#27](https://github.com/olafkfreund/PFactory/issues/27) | Task 11: Triager update-vs-create + catalog mutation (3-step `lookup_by_ac` decides UPDATE-in-place / CREATE-new / SKIP-locked) | [`cd5396b`](https://github.com/olafkfreund/PFactory/commit/cd5396b) (5 commits) | +24 |
| [#31](https://github.com/olafkfreund/PFactory/issues/31) | Task 15: `LaneStatusGrid` full reskin (5 independently lit lanes) + `pfactory init` / `pfactory migrate v0_1_catalog` CLIs | [`f4eb9aa`](https://github.com/olafkfreund/PFactory/commit/f4eb9aa) (5 commits) | +21 backend + 27 frontend |
| [#32](https://github.com/olafkfreund/PFactory/issues/32) | Task 16: Test evidence capture (screenshots / video / trace / HAR) + retention enforcer + portal endpoint + frontend Evidence tab | [`654e77a`](https://github.com/olafkfreund/PFactory/commit/654e77a) (5 commits; commit 4 deferred for follow-up PR) | +86 |

## v0.2.0 — released 2026-05-29

All 16 v0.2 tasks merged to `main` and the Task 16 deferred commit 4
follow-up (Triager PR-comment evidence-links, `5d8f588`) landed
immediately afterwards. Annotated tag `v0.2.0` pushed; GitHub Release
live at
<https://github.com/olafkfreund/PFactory/releases/tag/v0.2.0>.
Release body mirrors the v0.2.0 CHANGELOG section.

The Triager follow-up was the only piece held back during the parallel
batch (it block-waited on Task 11). It now ships portal evidence links
per accepted/flagged candidate in `triage_report.md` so PR reviewers
click straight from the comment to screenshots / video / trace.zip /
network.har.

## Test totals

| Snapshot | Backend tests | Δ vs prior | Notes |
|---|---:|---:|---|
| v0.1.0-mvp baseline (2026-05-28) | **531** | — | Walking skeleton — Python + pytest only |
| v0.2 in progress (post-batch-1, 2026-05-29) | **1039** (7 skipped) | **+508** | After 14 of 16 v0.2 tasks |
| v0.2 release candidate (post-batch-2, 2026-05-29) | **1170** (7 skipped) | **+131** | After all 16 v0.2 tasks — exceeded the +~110 forecast |
| v0.2.0 released (`5d8f588`, 2026-05-29) | **1177** (7 skipped) | **+7** | After Triager evidence-links follow-up |
| **v0.2 post-release (HEAD `bcc5c7d`, 2026-05-29)** | **1225** (7 skipped) | **+48** | Starlette `.content`→`.body` shim sweep + showcase + iframe demo |

The 7 skips are the docker-runner smoke tests (#23) — they require a
live daemon and gracefully skip when none is present.

## Pipeline status

```
  Planner ─► Gen-Functional ─► Executor ─► Evaluator ─► Triager
  (polyglot   (generic via      (DockerRunner    (5 signals;     (catalog-aware
   per #21)    descriptor #22)   + AppRuntime     null coverage;  in #27 →
                                  for Browser     TS primitives   evidence-links
                                  #24)            #25)            from 5d8f588)
```

All 16 v0.2 tasks shipped + the deferred Triager evidence-links
follow-up landed. The pipeline is now end-to-end demonstrable —
see the [live v0.2.0 showcase]({{ '/showcase/' | relative_url }})
running against [olafkfreund/pfactory-demo](https://github.com/olafkfreund/pfactory-demo).

## v0.2.0 demo + showcase

A live end-to-end run published at [`/showcase/`]({{ '/showcase/' | relative_url }}):

- **System under test:** [olafkfreund.github.io/pfactory-demo/](https://olafkfreund.github.io/pfactory-demo/) (Vite + React)
- **5 Playwright tests** generated from the 5-AC user story; **4 pass, 1 fails** (AC#5 surfaces a seeded cache bug)
- **Evidence captured:** 5 screenshots + AC#5 video.webm + AC#5 trace.zip
- **Planner output:** real [test_plan.json](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/test_plan.json) emitted via the Claude subscription (no API keys)
- **Tag:** [`v0.2.0-demo`](https://github.com/olafkfreund/PFactory/releases/tag/v0.2.0-demo) anchors the showcase commit (`be52f9b`)

## Health signals

- ✅ `scripts/verify-fork.sh --no-import` — passes against the v0.2 module set
- ✅ pytest (backend) — **1225 passed, 7 skipped** at HEAD `bcc5c7d`
- ✅ All 5 portal endpoints respond shim-compatibly without fastapi installed
- ✅ Pages site builds + deploys on every push to `main` (Cayman + custom layout)
- ✅ Showcase page renders the live SUT in an iframe + inline triage report with working evidence links
- ⚪ Docker integration — runner images build under CI; per-image smoke
  tests skip locally without a daemon (recovers via `pfactory-test`)
- ⚪ End-to-end smoke (`scripts/e2e-smoke.sh`) — 9 scenarios, operator-driven

## Cross-references

- Live demo + showcase → [Demo]({{ '/showcase/' | relative_url }})
- v0.2 driver doc → [`docs/plans/2026-05-28-enterprise-test-frameworks-design.md`](https://github.com/olafkfreund/PFactory/blob/main/docs/plans/2026-05-28-enterprise-test-frameworks-design.md)
- v0.2 task plan → [`docs/plans/2026-05-28-enterprise-test-frameworks-tasks.md`](https://github.com/olafkfreund/PFactory/blob/main/docs/plans/2026-05-28-enterprise-test-frameworks-tasks.md)
- Architecture → [Architecture]({{ '/architecture/' | relative_url }})
- Changelog → [CHANGELOG.md](https://github.com/olafkfreund/PFactory/blob/main/CHANGELOG.md)
- v0.2.0 release → [github.com/olafkfreund/PFactory/releases/tag/v0.2.0](https://github.com/olafkfreund/PFactory/releases/tag/v0.2.0)
- v0.2.0-demo tag → [github.com/olafkfreund/PFactory/releases/tag/v0.2.0-demo](https://github.com/olafkfreund/PFactory/releases/tag/v0.2.0-demo)
- Epic + sub-issues → [github.com/olafkfreund/PFactory/issues](https://github.com/olafkfreund/PFactory/issues)
