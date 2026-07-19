# Reddit post

Suggested subreddits: r/programming, r/devops

---

**Title:** Planning that reads your code: an AI planner that clones the repo before it writes a plan, and a test gate that refuses to certify a failing build

**Body:**

Most AI planning tools take a prompt and produce a plausible plan. It reads well because it is generic — it has never seen the repository it is planning against. We took the opposite approach in PFactory, the "Plan" stage of a four-service autonomous pipeline (Plan, Build, Test, Cockpit) that takes a GitHub issue in and produces a tested pull request out.

Before PFactory writes any plan, it:

- Clones the target repo read-only and builds a RepoMap: languages, frameworks, package managers, infrastructure-as-code, and the exact commit it read.
- Classifies the change (in our recent runs, `change_mode = modify` — an edit to existing code, not greenfield).
- Grounds the plan in the repo's real delivery history (a DORA pass), plus a house-standards and live Backstage catalog enrichment pass.
- Runs an injection scan over the inputs.
- Scores its own feasibility and architecture review lenses (1.0/1.0 this cycle).

The output is a signed Task Contract with explicit acceptance criteria about that specific codebase, not a guess. That contract then drives the build (each task builds in a throwaway Kubernetes Job, then opens its own PR) and the test stage (autonomous test generation, run in a per-task sandbox, graded on coverage, stability, mutation, semantic relevance, and CI parity, then assigned a Verification Assurance Level).

The part worth scrutinizing: the honesty gate. In one run a `slugify` helper built and looked fine but failed one of twelve test verdicts on a unicode edge case. The gate capped it at VAL-0 and auto-filed a handback instead of certifying it. It refuses to show a green test checkbox unless a real test runner actually executed. The clean run that followed (a `clamp` helper) verified to VAL-1: 5/5 acceptance criteria, 9 tests kept, 0 rejected, mutation probe killed, confidence 0.96, stable across 3 runs — with VAL-2/VAL-3 correctly reported as not-run because no API/integration/browser lane applies to a pure function.

One honest caveat from the same run: the verdict is computed correctly, but its auto-post back to the PR is gated by a fix we are now tracking as an issue. Naming it rather than hiding it.

**Short FAQ**

- *Is this just wrapping an LLM in a loop?* No. The point is separating planning from execution so there is a place for a governance gate and acceptance criteria before code is written, and a plan grounded in the actual repo rather than the prompt.
- *What does "reads your code" actually mean?* A read-only clone plus a RepoMap of languages/frameworks/package managers/IaC at a pinned commit, used to classify the change and shape the plan.
- *What is a VAL?* Verification Assurance Level — a ceiling recomputed from real test signals. A failing lower lane caps it; an untested dimension is reported as an honest gap, never a silent pass.
- *Can I see it run?* A live walkthrough of all four portals is available on request.
