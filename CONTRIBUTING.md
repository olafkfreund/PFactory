# Contributing to PFactory

Thanks for your interest! This guide covers everything you need to send a PR.

## TL;DR

1. Fork → branch from `dev` → make your change → PR back to `dev`.
2. Sign your commits (`git commit -s`) and follow conventional-commit subjects.
3. `pre-commit` and CI must be green before review.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating you agree to uphold it.

## How to ask for help

- **Questions / discussion** → [GitHub Discussions](https://github.com/olafkfreund/PFactory/discussions) (or open a `question` issue)
- **Bugs** → [Bug report issue template](https://github.com/olafkfreund/PFactory/issues/new?template=bug_report.yml)
- **Security issues** → see [SECURITY.md](SECURITY.md) (do **not** open a public issue)

## Development setup

Prereqs: **Python 3.12+**, **Node.js 24+**, **git**, **uv** (`pip install uv`).

```bash
git clone https://github.com/olafkfreund/PFactory.git
cd PFactory
npm run install:all                  # backend + web-server + frontend deps
cp apps/backend/.env.example apps/backend/.env       # add provider keys
cp apps/web-server/.env.example apps/web-server/.env # optional overrides
claude setup-token                                    # OAuth token for Claude SDK
```

Run the stack:

```bash
# Terminal 1
cd apps/web-server && .venv/bin/python -m server.main
# Terminal 2
cd apps/frontend-web && npm run dev
```

Open `https://localhost:3115`.

## Branching workflow

| Branch         | Purpose                                  | PR target |
|----------------|------------------------------------------|-----------|
| `feature/*`, `fix/*`, `chore/*` | Your work | `dev`     |
| `dev`          | Integration branch — pre-release work    | `main`    |
| `main`         | Stable; tagged releases cut from here    | —         |

Hotfixes can PR straight to `main` but require a maintainer review.

```bash
git checkout dev && git pull
git checkout -b fix/short-description
# work
git commit -s -m "fix: brief subject in imperative voice"
git push -u origin fix/short-description
gh pr create --base dev
```

## Commit messages

[Conventional commits](https://www.conventionalcommits.org/), single-line subject ≤ 72 chars, imperative voice.

```
feat: add task-creation wizard
fix: handle empty SDK response in insight extractor
docs: clarify Docker macvlan setup
chore: bump dependabot cadence to weekly
```

Sign every commit with the **Developer Certificate of Origin** (`-s`). PRs without sign-off will be asked to amend.

## Code style

Enforced by `pre-commit` and the CI workflow (`.github/workflows/ci.yml`):

- **Python** — `ruff check` + `pytest`
- **TypeScript / React** — ESLint + `tsc --noEmit`
- **Versions** — `package.json` is the source of truth; `.husky/pre-commit` syncs the others

Install hooks once:

```bash
npm install                # installs husky
pre-commit install         # if you also want the python pre-commit framework
```

## Tests

```bash
# Backend
apps/backend/.venv/bin/pytest tests/ -v
# Skip slow ones
apps/backend/.venv/bin/pytest tests/ -m "not slow"
# Frontend
cd apps/frontend-web && npm run lint && npx tsc --noEmit
```

Add coverage with the change. Bug fixes need a regression test.

## Concurrency / execution model

PFactory runs `process()` (the planning pipeline) as a **bounded pooled worker**,
not a k8s Job-per-task. This is the deliberate Phase-2 execution model under
[RFC-0016](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0016-horizontal-concurrent-execution.md)
§5(c) — issue #218.

- `process_async` offloads the blocking pipeline off the event loop with
  `asyncio.to_thread` (#219), so the single uvicorn loop keeps serving
  `/api/health` and other sessions while a plan is processed.
- Concurrency is bounded by an admission cap (`PFACTORY_MAX_CONCURRENT_PLANS`,
  default 4) whose state is **durable in Postgres** and granted via a
  `SELECT ... FOR UPDATE` slot transaction, so the cap holds **across replicas**
  and survives a restart (#220). With `DATABASE_URL` unset it degrades to an
  in-process semaphore and logs that it is not multi-replica safe.

**Why not a Job per plan?** Default planning is deterministic and sub-second
(no LLM). A k8s Job's spin-up (image pull, pod schedule, warm nix-store mount)
costs seconds — far more than the work it would run — so Job-per-plan would make
planning slower with no isolation benefit. The two hazards a Job would solve
(event-loop starvation, unbounded fan-out) are already solved above.

If a genuinely heavy or governed (LLM) planning path is ever added, wire an
**opt-in, env-gated** Job path for *that path only* using the shared
`scripts/job_dispatch.py` builder and `apis/concurrency-conventions.md` §3 in the
[Factory](https://github.com/olafkfreund/Factory) repo. Do not put sub-second
deterministic planning behind a Job pod.

## PR checklist

The full template lives in `.github/PULL_REQUEST_TEMPLATE.md` — TL;DR:

- [ ] Targets `dev` (or `main` for hotfix)
- [ ] Subject follows conventional commits, body explains *why*
- [ ] Pre-commit + CI pass
- [ ] Tests added or updated
- [ ] Behind a feature flag if incomplete
- [ ] Breaking changes called out

Keep PRs **focused and < 400 lines** when you can — easier to review, faster to merge.

## Releases

See [RELEASE.md](RELEASE.md) — version bumps via `node scripts/bump-version.js {patch|minor|major}` on a branch, then PR to `main` triggers tag + GitHub Release.

## Maintainers

Branch protection on `main` and `dev` is declared as code in the Factory hub, in
[`scripts/apply_branch_protection.sh`](https://github.com/olafkfreund/Factory/blob/main/scripts/apply_branch_protection.sh)
— one engine covering all four service repos plus the hub and gitops, rather than
a copy per repo that drifts on its own. From a Factory checkout:

```bash
scripts/apply_branch_protection.sh --repo PFactory           # CHECK: report drift, write nothing
scripts/apply_branch_protection.sh --apply --repo PFactory   # WRITE the declared intent
```

Check is the **default**: it reads the live configuration, diffs it against the
declared intent, and exits non-zero on any divergence without changing anything.
Applying requires the explicit `--apply`. Either mode needs a token with admin on
the repo, because reading branch protection is an admin-only endpoint. A scheduled
job in the hub runs check mode across the fleet daily, so drift surfaces without
anyone having to remember to look.

What is protected:

| | `main` | `dev` |
| --- | --- | --- |
| Required CI checks | `backend (ruff + pytest)`, `critical (fast PR gate)` | same |
| Branch must be up to date | yes | no |
| Approving reviews | 1 | none |
| Code-owner review | yes | no |
| Conversation resolution | yes | no |
| Force-push / deletion | blocked | blocked |

`dev` requires no review deliberately. It is the default branch and the one PRs
target, and a solo maintainer — or one of the factory's own agents — has nobody to
approve their own PR, so requiring one there would stall every merge; `strict`
would additionally force a rebase before each one. The CI checks are *not*
relaxed on `dev`: it is looser about review, never about tests. `main` keeps the
full set because it is the release branch and only receives promotion merges from
`dev`.

This repo used to carry its own `scripts/setup-branch-protection.sh`, described
here as idempotent. It was not. It required `frontend (typecheck)` instead of
`critical (fast PR gate)`, so it would have swapped which gate blocks a merge, and
it applied `main`'s payload to `dev` as well, so running it as these instructions
said would have reimposed a review requirement on the integration branch and
reverted a deliberate decision, with no warning that it had. It has been deleted
rather than corrected in place: three divergent copies of one policy, with nothing
comparing any of them to the live configuration, is what produced that bug
(Factory#468).

## License

By contributing you agree your work is dual-licensed under the project's terms (MIT or GPL-3.0 at the recipient's option) — see [LICENSE](LICENSE), [LICENSE-MIT](LICENSE-MIT), and [LICENSE-GPL](LICENSE-GPL).
