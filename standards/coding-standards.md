# Factory Coding Standards

> Status: Active - enforced from 2026-06-20
> Authority: program-wide. Applies to all five repos - Factory (hub),
> PFactory, AIFactory, TFactory, CFactory.

This is the single normative standard for code and code structure across the
Factory fleet. It exists because a fleet-wide review found two systemic problems
that dwarf any local nit: **no enforced quality gates** (no repo ran mypy; ruff
configs were minimal or absent; the security, dead-code, datetime and complexity
rules were unenforced everywhere) and **duplication by design** (192 byte-identical
Python files, ~28,881 LOC, copied between PFactory and AIFactory; `gh_client.py`
and `rate_limiter.py` byte-identical across three repos and already drifting). The
goal of this document is to make the strict bar real, enforced, and consumed from
one place.

## 0. Scope and authority

- **Strict rules from now on.** New code MUST pass the full bar. Legacy is fixed
  on touch under a **ratchet**: gates run on the PR diff and may not regress a
  changed file. Untouched legacy hotspots are allowed until touched.
- **One source of truth.** Thresholds live in ONE versioned shared lint-config
  in this hub ([`standards/`](.)). Per-service configs may only **TIGHTEN**, never
  loosen. A config-lint CI check enforces tighten-only.
- **Shared logic is consumed, not copied.** Beyond the rule of three, shared
  logic moves to a pinned, versioned package and is consumed via semver - never
  git-copy or vendor-by-hand. A `jscpd` cross-repo gate fails the next paste.
- All gates are **blocking** under branch protection.

## 1. Python (3.11+)

1.1 **Lint.** A single `ruff` config with the explicit select set
`E,F,W,I,N,UP,B,C4,S,SIM,RUF,PTH,TID,ASYNC,A,DTZ,T20,ARG,ERA,PL` (curated `PL`
including `C901,PLR0912,PLR0913,PLR0915`). No bare `ruff check`, no blanket
category ignores. The shared baseline is [`standards/ruff.toml`](./ruff.toml).

1.2 **Types.** `mypy --strict` over the whole package as a BLOCKING gate
(`disallow_untyped_defs`, `disallow_any_generics`, `warn_return_any`,
`warn_unused_ignores`). Baseline: [`standards/mypy.ini`](./mypy.ini).

1.3 **Suppressions.** No bare `# noqa` / `# type: ignore` / `cast()`. Every one
carries a specific code, a one-line reason, and an issue ref - e.g.
`# type: ignore[arg-type]  # upstream stub bug, see #123`. Enforced by `PGH003`
/ `PGH004` and `warn_unused_ignores`.

1.4 **Security sinks (`ruff S`).** No `shell=True` on non-constant input; no
`os.system`/`os.popen`; no `eval`/`exec`; no `pickle`/`marshal`/`yaml.load` on
untrusted data; no string-built SQL (parameterize / use the ORM); no XML parsing
without `defusedxml`.

1.5 **Secrets.** One typed `pydantic-settings` boundary per service; no scattered
`os.environ` reads past that boundary. No secrets in source, tests, or fixtures.

1.6 **Errors.** No bare `except`; no `except Exception: pass/continue/return None`
silent swallow (`BLE001`/`S110`/`S112`). Narrow the exception and either
log-and-degrade or propagate. Silent swallowing is the documented cause of prior
false-success builds, so this is a hard rule.

1.7 **Typed boundaries.** `pydantic` v2 models / `TypedDict` at all I/O; no
`dict[str, Any]` past a seam; `Protocol` for mockable callables.

1.8 **Structural caps.** File <= 400 lines; function <= 50 logical lines; <= 5
params; cyclomatic <= 10; cognitive <= 15; nesting <= 3 (use guard clauses). No
god-files.

1.9 **Hygiene.** `pathlib` over `os.path`; tz-aware datetimes (`DTZ`), no naive
`now()`/`utcnow()`; `logging`, not `print()`, in service code (`T20`).

1.10 **Dead code and comments.** No commented-out code (`ERA`); no unused
imports/vars/args (`F401`/`F841`/`ARG`); no `if False`. Comments explain WHY, not
WHAT; no banner/divider comments; no per-commit/per-task changelog narration in
docstrings.

## 2. TypeScript (Node + React)

2.1 **tsconfig full strictness.** `strict` plus `noUncheckedIndexedAccess`,
`exactOptionalPropertyTypes`, `noImplicitOverride`, `noFallthroughCasesInSwitch`,
`noImplicitReturns`, `verbatimModuleSyntax`. Never re-open holes
(`noImplicitAny:false` etc. are forbidden). Target ES2022+. The
`compilerOptions` are snapshot-tested so a PR cannot weaken them. Baseline:
[`standards/tsconfig.base.json`](./tsconfig.base.json).

2.2 **ESLint 9 flat config**, `typescript-eslint` `strictTypeChecked` +
`stylisticTypeChecked` (type-aware), `react-hooks` recommended-latest, `jsx-a11y`.
Run `eslint --max-warnings=0`.

2.3 **Ban `any`/implicit-any, non-null `!`, `as unknown as T`,** and as-casting
untrusted JSON. Validate every boundary (HTTP/WS/env) with Zod/valibot, infer
types inward.

2.4 No floating/misused promises; errors caught as `unknown`; throw `Error`
subclasses; no empty `catch`.

2.5 **Structural caps.** Files <= 400, functions <= 60, complexity <= 10,
max-params 4, max-depth 4.

2.6 **React.** Pure components; Rules of Hooks; honest `exhaustive-deps` (no
disable); minimal/local state; no `useEffect` for derived state.

2.7 Prettier owns formatting only; ESLint carries no stylistic rules.

2.8 No dead code / unused deps / unused exports (`knip`). Behavior-asserting
tests (query by role/text); no snapshot-only or tautological tests.

## 3. Cross-cutting (all languages)

3.1 **Unified size/complexity caps:** file 400; function 50 (Py) / 60 (TS);
cyclomatic 10; cognitive 15; nesting 3; params 5 (Py) / 4 (TS).

3.2 **No duplicated code.** `jscpd` cross-repo gate; clones of >= 8 lines / 50
tokens fail; the duplication budget ratchets down. Rule of three -> extract to a
shared lib consumed via pinned semver.

3.3 **No grab-bag modules.** No `utils`/`helpers`/`misc`/`common` dumping
grounds - name modules by domain capability. Class <= 10 public methods.

3.4 **Zero-warning policy.** No blanket file-level disables; every suppression
has a reason and an issue ref.

3.5 **Security baseline gate (blocking):** `gitleaks`/`trufflehog` secret scan;
`semgrep` AST rules (ban bare except, `eval`); `trivy`/`grype`/`osv` dependency +
license scan. Constant-time secret comparison (`hmac.compare_digest`) everywhere.

3.6 Formatting is auto-applied, never reviewed. `.editorconfig` at every repo
root ([`standards/.editorconfig`](./.editorconfig)).

3.7 One source of truth for thresholds in this hub; tighten-only overrides.

## 4. CI / pre-commit enforcement

4.1 `pre-commit` is the single local+CI entrypoint (same config both places).

4.2 **Python CI job (blocking, branch-protected):** `ruff check --no-fix`,
`ruff format --check`, `mypy --strict`, `pytest-cov --fail-under`, and run each
reference module's self-test.

4.3 **TS CI job (blocking):** install `--frozen-lockfile`, `tsc --noEmit`,
`eslint --max-warnings=0`, `prettier --check`, `vitest`, `knip`.

4.4 **Fleet jobs:** `jscpd` cross-repo duplication gate; `gitleaks`; dep/vuln
scan; config-lint (each service extends the pinned shared baseline; no rule
downgraded).

4.5 **Pin the toolchain:** committed lockfiles; Node via `engines`/`.nvmrc`/
`packageManager`; `ruff`/`mypy` versions pinned.

4.6 **Ratchet:** gates run on the PR diff; legacy hotspots are allowed until
touched.

## 5. How to consume the shared baseline

Each service extends the hub baseline and may only tighten:

```toml
# pyproject.toml (per service)
[tool.ruff]
extend = "path/to/factory-standards/ruff.toml"   # pinned hub baseline
# service-specific TIGHTENING only below
```

See [`standards/README.md`](./README.md) for the consumption mechanism (pinned
vendored copy with a drift gate today; published package once `factory-core` is
extracted - epic Factory#154).

## 6. Adoption

Tracked by epic **Factory#154** (fleet code-quality hardening). This doc + the
shared baseline configs are Phase 0; per-service adoption (blocking CI) and the
shared-library extractions that kill the duplication are the child issues.
