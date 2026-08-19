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
Aliased imports from one module go in ONE statement (`combine-as-imports`):
ruff's default splits them one statement per alias, which manufactures
byte-identical prologues in any two files importing the same helpers and put the
import-sort rule in direct conflict with the jscpd clone budget (Factory#415).

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

3.8 **Before fixing anything security-shaped, find every copy.** Search all six
repos for the file BEFORE editing it, fix the hub canonical where one exists,
and re-vendor. On 2026-08-13 a single sweep found **seven** fixes that existed
in one repo while siblings carried the bug: `artifact_store.py` tarslip, the
SSRF guard, workspace lock `0o644`, `skills_service` pickle-vs-JSON,
`bump-version.js` fs-race, `mask_secret`, and rule 4.10 of THIS FILE. Scanners
report per-repo, so an unpropagated fix reads as a clean count next door.

3.9 **A guard is finished when every sink calls it, not when it passes its
tests.** Count the sinks, not the tests. Twice on 2026-08-13 a correct,
mutation-checked SSRF guard shipped wired into ONE of fourteen call sites, in
two repos, by different authors. Every signal was green and the product was
open. After adding a guard, grep the sink (`httpx`/`requests`/`urlopen`,
`subprocess`, path joins) and diff that list against the guard's callers.

3.10 **Never validate a URL and then hand the fetch to someone else.** A guard
on the initial URL is void if the fetcher follows redirects: the dangerous URL
is the one you never see. Own the fetch with redirects disabled, or re-validate
every hop against the same guard. Applies equally to an SDK, a subprocess, or a
library that retries.

3.11 **Test a redaction against windows of the secret, not the whole value.**
`assert SECRET not in output` passes while the first twelve characters ship.
Assert no 4- or 6-character window of the credential appears in the RENDERED
sink (the log record, the response body, the file bytes). On 2026-08-13 a suite
was green for months with `mask_secret` returning short secrets verbatim -
because two of its own assertions had pinned the leak as correct behaviour.

3.12 **A test may not read or write outside the repo.** Point every cache, home
and config path at a tmp fixture. A `SkillsService` suite read the developer's
real `~/.aifactory/` cache, so a broken parser tested GREEN (the cache
short-circuited it), and a mutated run POISONED that cache so a later run of
correct code failed. A suite that can go green from a file outside the repo
invalidates every other gate that trusts it.

3.13 **A test fixture must not match a real credential pattern.** Secret
scanners match on SHAPE, so a fabricated `sk-proj-...` in a test file raises a
real alert and can hard-block a push. On 2026-08-13 a fixture for a
token-at-rest test paged the operator for a value with 8 distinct characters
over 48. Build fixtures that cannot match: a clearly-fake prefix, or assemble
the realistic prefix at runtime (`"sk-" + "ant-"`) when the code branches on it,
with a comment saying why - or someone will helpfully make it realistic again.
The cost of getting this wrong is not the alert; it is that the next real one
gets ignored, and that bypassing push protection becomes a habit.

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

4.7 **A gate that cannot run must fail, never pass.** If a hook cannot resolve
the tool it needs, it exits non-zero and says what it looked for. Wrapping the
checks in `if [ -n "$TOOL" ]` turns a missing binary into a silent green
commit, which is worse than having no hook at all: the absent gate is visible,
the skipped one is not. An opt-out is allowed only as an env var a developer
sets deliberately, never as the fallback. Same rule for the executable bit -
`core.hooksPath` without a `.husky/_` wrapper means git skips a non-executable
hook without a word, so hook files are committed `100755` and a test asserts
it. The rule is about gates, not only hooks: a CI job that cannot reach the
input it compares against - a baseline checkout that 404s, a fetch that times
out, a missing token - has not verified anything, so it exits non-zero. An
unverifiable baseline is not a verified one. `continue-on-error: true` paired
with an `if: steps.x.outcome == 'success'` diff, or a fetch loop that
`continue`s past a failed download, reports the same green as a real pass
while a job named "blocking" blocks nothing.

4.8 **Hooks must scrub git's exported environment before running anything that
shells git.** During a commit git exports `GIT_DIR`, `GIT_INDEX_FILE`,
`GIT_WORK_TREE`, `GIT_PREFIX`, `GIT_CONFIG_PARAMETERS` and friends to the hook.
Any child process that runs git - a test suite with repo fixtures, a `git
worktree add`, a lint helper - inherits them and operates on the REAL repository
instead of its own. Observed: a fixture's `git add -A` staging 2,136 deletions
into the repo mid-commit, a fixture's `git branch -M main` clobbering the local
branch, and a `git worktree add` emptying the caller's index so the commit being
gated became empty. Scrub once at the shared boundary (the hook, or the test
suite's root `conftest.py`), not per call site. Commands that must read the
in-flight commit - `git diff --cached` - are the deliberate exception and keep
the exports.

4.9 **Prove a gate both ways.** A check is verified only when a violation makes
it fail; passing on clean input proves nothing, and every "fix" that merely
made a gate permissive would have passed that half. Each gate leaves behind a
test for both directions: a change carrying pre-existing debt is accepted, and
one new violation is rejected.

4.10 **Assert on the artefact, not on the process.** A control that reports
whether it *ran* cannot distinguish a clean system from one where nothing
happened. Ask of every gate:

> **If this had done nothing at all, would the output look different?**
> If no, the control is not evidence.

Factory#642 catalogues **seven** instances, found on 2026-08-07 by three agents
working separately on unrelated issues. An **eighth** turned up on 2026-08-10
while wiring the Fides change gate (Factory#619, Factory#541) — also by
accident, also by someone working on something else, which is the point.
Different subsystems, one mechanism: **the status channel reported on the
process rather than on what it produced.** Seven were quiet and sat; the one
that failed loudly was fixed the same day. The severity ordering was set by
visibility, not by risk.

The question above is cheap to quote and expensive to apply, so it does not
travel alone. **What makes it executable is knowing which artefact to read** —
that knowledge, not the question, is the deliverable:

| Control | Do NOT trust | Read this instead |
|---|---|---|
| Signature verify | `unverified image` | the message text: `ghcr.io/token` + `UNAUTHORIZED` is a read failure, not a verdict |
| Admission webhook | the admit | the `kyverno.io/verify-images` annotation, and that it **names the image** |
| PolicyReport board | absence of `fail` | the result **count**, and whether the rule produced any row at all |
| `kyverno test` | the pass | that the case actually **evaluated** — a case with no result scores as a pass |
| Merged PR | `merged: true` + green CI | `commits:` vs what you pushed, **and** the file bytes on `main` |
| Patch / script | the success message | the patch's **exit code**, checked before the message prints |
| ArgoCD selfHeal test | `Synced` | that the field you changed is **git-managed** — an added annotation is never reverted |
| Installer / fetch step | the step's green | that the thing is **runnable afterwards** (`command -v`), and the download's own exit status |

Three traps carried from the instances, each of which cost an hour or more:

- **`curl … | sh` in CI hides the DOWNLOAD's failure.** The step can still fail —
  if `sh` exits non-zero it does. What it cannot report is curl failing, and that
  is the direction that matters, because it fails *open*. A default `run:` on
  Linux is `bash -e {0}`, with no `pipefail`, so only the last element's status
  survives; `sh` reading empty stdin exits 0. A 404 installer therefore leaves
  the step green having installed nothing, and the real failure surfaces later as
  `command not found`. Setting `shell: bash` explicitly is not cosmetic — it
  selects `bash --noprofile --norc -eo pipefail {0}`, which would surface this
  one. Better still: fetch to a file, check the status, then assert the binary
  runs.
- **`git merge-base --is-ancestor` is useless in a squash-merge repo.** It
  returns non-zero for every correctly merged PR. Compare `headRefOid` to the
  SHA you pushed.
- **`commits:` and a content read are a pair, not alternatives.** `commits:`
  catches a commit that never arrived; only a content read catches a commit that
  arrived having eaten someone else's line during a rebase. **Counts survive a
  rebase; content does not.**

A corollary for acceptance criteria: **do not phrase one as "X is quiet".**
Silence is exactly what a control that never ran produces. State what artefact
must exist and what it must say.

4.11 **Security rules are enforced WHOLE-REPO; only style may be diff-scoped.**
Diff-scoped enforcement ("legacy is fixed on touch") is correct for style debt
and wrong for security sinks, because *untouched code is where old
vulnerabilities live* - "fixed on touch" means never for a file nobody opens.
On 2026-08-13 a `pickle.load` on a user-writable cache - a live RCE primitive,
already fixed in a sibling - survived for months although rule 1.4 banned it
and ruff `S` was enabled: the gate only ever looked at changed files. The
numbers make the split cheap: 6,436 strict violations repo-wide is unlandable,
but the high-signal security subset was **103 fleet-wide**. Security rules get a
blocking whole-repo gate with a per-finding allowlist (path, rule, reason, issue
ref) that can only ratchet down.

4.12 **An exclusion needs a replacement asking the same question.** Suppressing
a scanner rule is permitted ONLY when paired with a barrier-aware query covering
the same sinks; an `exclude:` with no twin is silencing. Prove the replacement
still reports: build the analysis over the UNFIXED tree and confirm it flags the
same sites the stock rule does. Never barrier a check that does not establish
the property - an "the file exists" test says nothing about WHICH file.
Corollary from 2026-08-12: a scan's breadth is part of its result. Four repos
reported near-zero because they ran the default suite; levelling to
`security-and-quality` took the fleet 1,526 to 3,876 with no code change. Never
compare alert counts across repos without checking the suite.

4.13 **The unfixed-tree check in 4.12 is NOT sufficient. Delete the sanitizer
and re-run STOCK.** If stock's count collapses to what your barrier reports,
your barrier IS that deletion in disguise - it silences without fixing. The
4.12 check passes trivially whenever the helper being barriered did not exist
on the base tree, which is precisely the common case. Three barriers were
proposed on 2026-08-13 and all three failed this test, none the old one:

| proposed barrier | what it actually did |
|---|---|
| project-registry lookup | matched 93 nodes, cleared **0** alerts (119 -> 119) |
| `client_error` | cleared 104, and all 104 were the ONE branch that must not be barriered - the two sink lists were byte-identical at 18 |
| `confine_to_workspace` on registry paths | a **runtime no-op**: the allowlist contains the value being checked |

Three corollaries, each earned the same day:

* **"This barrier clears N alerts" is usually counted per SOURCE.** Each sink is
  reached by many sources; removing one from a sink that has thirteen others
  leaves the sink reported. One estimate said ~42 and delivered 0.
* **A guard whose allowlist is derived from the same data it guards cannot
  reject that data.** Ask what populates the allowlist before trusting it. The
  fix is tiers, not a narrower single list: #553 split `_allowed_roots()` into
  `browse_roots()` (workspace root + registry, for a path that is not
  registered YET) and `registered_project_roots()` (registry alone, for a path
  that must ALREADY be a project), and gave each call site the one it needs.
  Note what that did NOT achieve -- a registry-derived value is still inside
  itself under both tiers, so the three `routes/github.py` alerts stayed open.
  Splitting a self-authorizing allowlist buys real confinement at the sites
  fed by a REQUEST, never at the sites fed by the allowlist's own source.
* **A helper with two branches is not a sanitizer.** Barriering the call node
  covers the unsafe branch too.

When the honest answer is "the fix is code, not a query", say so and record the
measurements in `.github/codeql/VALIDATION.md` so the next reader does not
re-derive it and reach the other answer.

4.14 **Publish prose from a file, then diff the file against what was
published.** Rules 4.10-4.13 gate code. Nothing gates the sentences that assert
the code is gated - and those sentences are what people act on. An issue body is
what someone builds from; a PR description is what a reviewer reads instead of
the diff; a status report is what a decision gets made on. Code has a check that
runs whether or not anyone remembers. Prose has a reader who may not exist.

Seven instances in a single session on 2026-08-13, none of them in code
(Factory#739), on top of the seven in systems that produced 4.10 (Factory#642).
Both agents failed it while actively discussing it with each other, and the
worst instance was inside the issue documenting the other six. A discipline two
people cannot hold for the length of one conversation, while it is the explicit
topic of that conversation, will not be held by anyone under deadline. So it has
to be mechanical:

```sh
gh issue create --body-file body.md ...
diff body.md <(gh issue view N --json body -q .body)   # trailing newline only
```

`--body-file` is safe-by-construction against shell mangling AND it leaves a
source of truth to compare against. `--body "..."` has neither property, and
leaves nothing to diff, so damage is findable only by reading.

Grepping for the claim you replaced is the weaker fallback, and it must run in
BOTH directions - old phrase absent, new phrase present - for the same reason an
empty file passes any "must not contain" test and an alerts endpoint returning
`[]` means both "clean" and "not analysed yet". A negative result proves nothing
unless something positive pins it. But grep only finds damage you predicted: the
instance that forced this rule was two inline-code spans **deleted** before
publication, and nobody greps for text they did not know had gone. Diff asks the
strictly stronger question - is the published artefact the thing I wrote? - and
falls out of a flag you should be using anyway.

Corollary, earned the same day: **a correction is landed when it replaces the
claim, not when it follows it.** Appending a comment preserves the record and
feels like the careful choice, but an issue is read as a statement of what is
true, not as a transcript. Nobody scrolls. The comment is for provenance; the
body is the claim. Two of the seven were corrections filed as comments under
bodies that kept the refuted assertion.

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

5.1 **This document is vendored too.** A rule nobody can read locally reaches
nobody: an agent or a developer working in a service repo does not open the hub.
So every service vendors `coding-standards.md` alongside the configs, and the
drift gate compares it like any other vendored file. Copies are byte-identical
to the hub - no provenance header, because the comparator for a Markdown file
cannot strip leading `#` lines without also blinding itself to every heading.

5.2 **One pin filename for a vendored DIRECTORY: `.hub-sha`, beside it.**
`standards/.hub-sha` holds the hub commit that whole directory was vendored
from, and it is the only thing tooling needs to read to answer "which hub is
this service on". The same filename is used for any other directory vendored
from the hub - `apps/backend/factory_common/.hub-sha` and so on.

The rule binds a directory whose CONTENTS ARE THE VENDORED SET. That is what
makes "beside it" a defined location. Two of the fleet's four hub-vendored sets
are not that shape: verification-core is six individual files across three roots,
and factory-ui is two files inside a components directory the portal otherwise
owns - a `.hub-sha` there would sit next to a hundred files it says nothing
about.

Those sets pin in their gate's workflow, and that is permitted **only while the
hub can read the pin without opening the workflow**. The original objection to a
workflow SHA was exactly that nothing outside the workflow could find it, and
that objection is answered by machinery, not by exemption:
`scripts/check_pin_freshness.py` declares every file-granular gate, reads all of
its consumers' pins daily, and fails if one is gating against a canonical that
has since moved. A gate absent from that list is a pin nobody can find, and the
exemption does not cover it. See Factory#514, Factory#519.

5.3 **The vendored set is `ruff.toml`, `mypy.ini`, `.editorconfig`,
`coding-standards.md`.** Adding a file to the hub does not add it to a service;
vendor the file and register it in that service's gate in the same change, never
one without the other.

## 6. Adoption

Tracked by epic **Factory#154** (fleet code-quality hardening). This doc + the
shared baseline configs are Phase 0; per-service adoption (blocking CI) and the
shared-library extractions that kill the duplication are the child issues.
