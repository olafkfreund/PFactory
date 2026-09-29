---
status: approved
issue: 801
spec: spec/2026-09-29-801-language-signal-ambiguity.md
---

# Plan: resolve the spec language by signal strength, not list order

Approved decisions, carried from the spec (implement these; do not re-derive):

- Three tiers replace the single ordered `_LANGUAGE_SIGNALS` list.
- `detect_spec_language_signal` keeps its signature and its
  `(language, matched_token)` contract. `detect_spec_language`, `boundary`,
  `LanguageReconcile` and `reconcile_language` are **unchanged**.
- A token several languages share (`gradle`, `android`) resolves to **`None`**, not
  a guess and **not** a repo-language tie-break — #585 requires a real conflict to
  HALT rather than resolve quietly.
- Only `apps/backend/plan/recon/language_reconcile.py` and
  `tests/test_recon_change_mode.py` change. `checks.py` and the downstream
  consumers (`testing_strategy.py`, `tfactory_block.py`, `delta.py`,
  `migration_classifier.py`) are not touched.

Measured already, quoted rather than re-run: the candidate resolver scores
**0 mismatches over 41 cases**, including every assertion the existing suite makes.

## Steps

1. In `apps/backend/plan/recon/language_reconcile.py`, replace `_LANGUAGE_SIGNALS`
   and `_SIGNAL_PATTERNS` with the three tables and their compiled patterns. Keep
   `boundary()` exactly as it is — it is #397's fix. Write the tables as:

        _LANGUAGE_NAMES: list[tuple[str, tuple[str, ...]]] = [
            ("rust", ("rust",)),
            ("go", ("golang", "go.mod", "goroutine", "gofmt")),
            ("typescript", ("typescript", "deno")),
            ("javascript", ("javascript", "express.js", "node.js", "nodejs")),
            ("python", ("python",)),
            ("java", ("java", "spring boot")),
            ("csharp", ("c#", ".net", "dotnet", "asp.net")),
            ("ruby", ("ruby", "rails")),
            ("php", ("php", "laravel", "symfony")),
            ("kotlin", ("kotlin",)),
            ("swift", ("swiftui",)),
            ("cpp", ("c++",)),
        ]

        _CTX_BEFORE = (
            r"(?:written\s+in|rewritten\s+in|ported\s+to|migrate[ds]?\s+to|in|using|with)\s+"
        )
        _CTX_AFTER = (
            r"\s+(?:service|module|package|binary|app|application|code|codebase|"
            r"version|program|library|sdk|backend|api)"
        )

        _WEAK_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
            ("go", ("go",)),
            ("swift", ("swift",)),
            ("typescript", ("ts",)),
            ("javascript", ("js",)),
            ("rust", ("rs",)),
            ("python", ("uv",)),
        ]

        _TOOL_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
            ("rust", ("cargo", "tokio", "actix")),
            ("python", ("pytest", "fastapi", "django", "flask")),
            ("java", ("maven",)),
            ("kotlin", ("jetpack compose",)),
            ("cpp", ("cmake",)),
        ]

        _SHARED_TOOLS: dict[str, tuple[str, ...]] = {
            "gradle": ("java", "kotlin", "scala", "groovy"),
            "android": ("java", "kotlin"),
        }

   Compile `_LANGUAGE_NAMES` and `_TOOL_SIGNALS` exactly as `_SIGNAL_PATTERNS` does
   today (`"|".join(boundary(n) for n in needles)`). Compile `_WEAK_SIGNALS` as, per
   needle, `f"{_CTX_BEFORE}{re.escape(n)}\\b|\\b{re.escape(n)}{_CTX_AFTER}\\b"`,
   joined with `|`.

2. Rewrite `detect_spec_language_signal`'s body only. Keep the existing text
   assembly (title + description + criteria + raw_text, lowered) verbatim, then:

   - tier 1: first `_LANGUAGE_NAMES` pattern that matches → `(lang, match.group(0))`
   - tier 2: first `_WEAK_SIGNALS` pattern that matches →
     `(lang, match.group(0).strip())` — `.strip()` because the context phrase
     carries surrounding whitespace, and the token is shown to the author as
     evidence (#397)
   - tier 3: first `_TOOL_SIGNALS` pattern that matches → `(lang, match.group(0))`
   - then: if any `_SHARED_TOOLS` token matches → `(None, None)`, with a comment
     saying the ambiguity is deliberate and why (#585)
   - fall through → `(None, None)`

   Keep the docstring's #397 explanation and add one paragraph on the tiers.

3. Update the module's table comment. The current one claims "boundaries also make
   the ordering non-load-bearing"; that was only ever true of substring collisions.
   Say what is actually true now: order within a tier does not matter because no two
   entries in a tier share a token, and cross-tier order **is** the design.

4. In `tests/test_recon_change_mode.py`, add one parametrised test,
   `test_the_spec_language_resolves_by_strength_not_list_order`, over a module-level
   table of the 41 `(prose, expected_language)` cases from the spec's "Measured"
   section, each row commented with the tier that should decide it. Leave every
   existing test in that file untouched.

5. Run `apps/backend/.venv/bin/pytest tests/test_recon_change_mode.py -q`. The new
   table and all existing tests must pass. If an existing assertion fails, **stop
   and report** — the spec's claim was that none would, so a failure means the spec
   is wrong, not the test.

6. Run the reproduction, after: a Kotlin+Gradle plan against a `kotlin` repo, and
   the `go` / `swift` prose plans against a `python` repo, through
   `reconcile_language(plan, repo_map, "modify")`. Expect `conflict=False` for all
   three: `resolved_language="kotlin"` for the first, `"python"` for the other two.

7. Negative control, not committed — revert each tier one at a time and confirm the
   table fails for that tier's cases **and only those**:
   (a) fold `_LANGUAGE_NAMES` into one ordered list with the tool tokens → the
       Kotlin+Gradle case fails;
   (b) compile `_WEAK_SIGNALS` as bare `boundary(n)` without the context →
       the `go` / `swift` prose cases fail;
   (c) return the first candidate of a `_SHARED_TOOLS` hit instead of `None` →
       the Scala / Android cases fail.
   Restore after each.

8. Run `apps/backend/.venv/bin/pytest tests/ -q -k "recon or language or synthesize"`
   for the wider consumers, then commit (the hook runs ruff, the ratchet and the full
   backend suite), push, and open the PR against `dev` linking intent, spec and plan.

## Tests

    apps/backend/.venv/bin/pytest tests/test_recon_change_mode.py -q
    apps/backend/.venv/bin/pytest tests/ -q -k "recon or language or synthesize"

Expected: all pass. The full backend suite runs in the pre-commit hook;
`backend (ruff + pytest)` and `critical (fast PR gate)` are the CI gates.

## Rollback

Revert the commit. Detection returns to first-match-wins over one list: Kotlin specs
read as Java, and any brief containing the word "go" conflicts with a non-Go repo.
No state, no schema, no data — the change is one module's tables and one test.
