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

Open `https://localhost:3100`.

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

After a fresh clone of the repo, run:

```bash
bash scripts/setup-branch-protection.sh
```

…to (re)apply branch-protection rules on `main` and `dev` via `gh api`. Idempotent.

## License

By contributing you agree your work is dual-licensed under the project's terms (MIT or GPL-3.0 at the recipient's option) — see [LICENSE](LICENSE), [LICENSE-MIT](LICENSE-MIT), and [LICENSE-GPL](LICENSE-GPL).
