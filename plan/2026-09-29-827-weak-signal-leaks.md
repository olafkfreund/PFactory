---
status: approved
issue: 827
spec: spec/2026-09-29-827-weak-signal-leaks.md
---

# Plan: gate the weak language tokens on evidence, not adjacency

Approved decisions, carried from the spec (implement; do not re-derive):

- Rules **A / B / C** replace `_CTX_BEFORE`, `_CTX_AFTER` and `_GAP`.
- The text is **no longer lowercased** before matching; patterns that should ignore
  case carry `re.I` explicitly. Tier-1 names never require case.
- `cargo` → rust, `flask`/`django` → python, `maven` → java move from
  `_TOOL_SIGNALS` into `_WEAK_SIGNALS`. `_TOOL_SIGNALS` keeps `tokio`, `actix`,
  `pytest`, `fastapi`, `jetpack compose`, `cmake`.
- `_SHARED_TOOLS` behaviour is unchanged (`gradle`, `android` → `None`).
- One residual stays and gets **no test row**: `"In Swift succession the jobs
  retried."` resolves via rule B. Closing it costs a genuine positive; on a hard gate
  a false negative is worse.
- Only `apps/backend/plan/recon/language_reconcile.py` and
  `tests/test_recon_change_mode.py` change. Below the coder-handoff threshold (2
  files), so this one is implemented in-session.

Measured already, quoted rather than re-run: 49/49 shipped rows, 9/9 reported leaks,
7/7 new cases, 12/13 adversarial.

## Steps

1. `language_reconcile.py` — replace `_CTX_BEFORE`/`_CTX_AFTER`/`_GAP` with:

        _STRONG_PREFIX = (
            r"(?:written\s+in|rewritten\s+in|rewrite\s+in|ported\s+to|"
            r"migrate[ds]?\s+to|implemented\s+in)\s+"
        )
        _BARE_PREFIX = r"(?:in|using|with)\s+"
        _LANG_NOUN = (
            r"(?:service|module|package|binary|app|application|code|codebase|version|"
            r"program|library|sdk|backend|api|microservice|project)"
        )
        # A qualifier may sit between the token and the noun only if it LOOKS like a
        # proper noun, acronym or version -- it must carry an uppercase letter or a
        # digit. A shape allowlist, not a word denylist: #822 used a ten-word denylist
        # and "and", "live", "reliable" walked through it (#827).
        _QUALIFIER = r"(?:\s+[\w.+#-]*[A-Z0-9][\w.+#-]*){0,2}"

        # Tokens that are ordinary English words as well as language names. Rule C
        # requires these Capitalised-but-not-ALL-CAPS, so "A Swift SPM library"
        # resolves and "A SWIFT MT103 service" (the banking network) does not.
        _ENGLISH_WORD_TOKENS = frozenset(
            {"go", "swift", "flask", "cargo", "django", "maven"}
        )

2. Same file — `_WEAK_SIGNALS` becomes:

        _WEAK_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
            ("go", ("go",)),
            ("swift", ("swift",)),
            ("typescript", ("ts",)),
            ("javascript", ("js",)),
            ("rust", ("rs", "cargo")),
            ("python", ("uv", "flask", "django")),
            ("java", ("maven",)),
        ]

   and `_TOOL_SIGNALS`:

        _TOOL_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
            ("rust", ("tokio", "actix")),
            ("python", ("pytest", "fastapi")),
            ("kotlin", ("jetpack compose",)),
            ("cpp", ("cmake",)),
        ]

3. Same file — compile the weak patterns per needle as three alternatives:

        alts.append(rf"(?i:{_STRONG_PREFIX}{re.escape(n)})\b")                     # A
        alts.append(rf"(?i:{_BARE_PREFIX})(?:{n.capitalize()}|{n.upper()})\b")     # B
        if n in _ENGLISH_WORD_TOKENS:                                              # C
            alts.append(
                rf"\b{n.capitalize()}\b(?![A-Z]){_QUALIFIER}\s+(?i:{_LANG_NOUN})\b"
            )
        else:
            alts.append(
                rf"(?i:\b{re.escape(n)}\b){_QUALIFIER}\s+(?i:{_LANG_NOUN})\b"
            )

   Note rule A wraps the **token** in `(?i:…)` too — "Ported to TS" must resolve, and
   a strong prefix is evidence at any casing. Getting this wrong was the one failure
   in my first measured run.

4. Same file — compile `_NAME_PATTERNS`, `_TOOL_PATTERNS` and `_SHARED_TOOL_PATTERN`
   with `re.I`, and in `detect_spec_language_signal` **stop calling `.lower()`** on
   the joined text. Lower only the returned token, so the author-facing evidence
   string keeps its current shape. Update the docstring: name #827, and say that B and
   C read original casing while tier 1 never requires it.

5. `tests/test_recon_change_mode.py` — add 28 rows to `_STRENGTH_CASES`: the 9 leaks
   (all `None`), the 7 new cases, and the 12 attack cases that pass. Comment each with
   the rule (A/B/C) or the tier that decides it. Do **not** add a row for the residual
   `"In Swift succession …"`. Leave every existing row and test untouched.

6. Add `test_the_derived_signal_union_still_canonicalises_every_token`: for each
   `(lang, needles)` in `_LANGUAGE_SIGNALS`, assert every needle maps to that same
   language in `migration_classifier._CANON` (multi-word needles excluded, as `_CANON`
   skips them). This is the check #801's deviation 7 shows I need rather than assume.

7. Run, in order:
   - `apps/backend/.venv/bin/pytest tests/test_recon_change_mode.py -q`
   - `apps/backend/.venv/bin/pytest tests/test_language_descriptor_paths.py -q`
   - `apps/backend/.venv/bin/pytest tests/ -q -k "recon or language or synthesize"`
   If an existing assertion fails, stop and report: the spec claims none will.

8. Reproduction, inverted: run the nine leak cases through
   `reconcile_language(plan, RepoMap(languages=["python"]), "modify")`. All nine must
   give `conflict=False`. Record before and after.

9. Negative controls, not committed — one per rule, each failing only its own rows:
   (a) drop the case requirement from rule B → the `in swift succession` /
       `in go-to-market` rows fail;
   (b) replace `_QUALIFIER` with `(?:\s+[\w.+#-]+){0,2}` → the `swift and reliable
       api` / `go live with backend` rows fail;
   (c) drop the `(?![A-Z])` from rule C → the SWIFT rows fail;
   (d) move `flask`/`cargo`/`django`/`maven` back to `_TOOL_SIGNALS` → their rows fail.
   Restore after each.

10. Commit (the hook runs ruff, the ratchet and the full backend suite), push, open the
    PR against `dev` linking intent, spec and plan. Then hand the diff to a fresh Opus
    reviewer with only the plan path and the diff, per the managed model split — on
    #801 that step found three defects my own controls missed, and this change is in
    the same file with the same blind-spot risk.

## Deviations recorded while implementing

1. **Control (b) fails one row, not the two the spec predicted.** Removing the
   uppercase-or-digit requirement from `_QUALIFIER` was supposed to fail the
   `swift and reliable api` and `go live with backend` rows. It fails only
   `Go to the api docs.` — because rule C additionally requires the English-word
   token to be *Capitalised*, and those two rows have a lowercase `swift`/`go`, so
   rule C never applies to them whatever the qualifier permits. Rule C's case guard
   subsumes part of the qualifier's job.

   So the qualifier requirement is **less load-bearing than the spec claimed**: it
   carries exactly one row, not two. It stays — one row is still a real leak
   ("Go to the api docs." would resolve as Go) — but the spec overstated it, and a
   reader comparing the control's output against the spec would rightly query it.

2. **28 rows became 33.** Writing them out by rule rather than by measured set added
   five cases the scratch harness had not probed: `In go we have a saying about
   naming.`, `We go GDPR compliant service-wide.`, `Go live on Friday.`,
   `Go to settings.`, `Go to the api docs.`. All pass; the last is the one control (b)
   depends on.

3. **Step 6's `_CANON` guard passes, and asserts more than planned.** It also checks
   that each canonical language name maps to itself, which the plan did not ask for
   but falls out of the same loop.

## Tests

    apps/backend/.venv/bin/pytest tests/test_recon_change_mode.py -q
    apps/backend/.venv/bin/pytest tests/ -q -k "recon or language or synthesize"

Expected: all pass. The full backend suite runs in the pre-commit hook;
`backend (ruff + pytest)` and `critical (fast PR gate)` are the CI gates.

## Rollback

Revert the commit. Detection returns to #822's denylist gap: the nine cases resolve
again, five of them HALTing a valid plan on a hard gate. No state, no schema, no data —
one module's patterns and one test file.
