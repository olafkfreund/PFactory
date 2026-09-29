---
status: approved
issue: 801
intent: intent/2026-09-29-801-language-signal-ambiguity.md
---

# Spec: resolve the spec language by signal strength, not list order

## Design

`_LANGUAGE_SIGNALS` becomes **three tiers**, consulted in order of how much a
match actually proves. `detect_spec_language_signal` keeps its signature and its
`(language, matched_token)` contract.

### Tier 1 — `_LANGUAGE_NAMES`: unambiguous identifiers

A match resolves immediately. Only tokens that name exactly one language:

    rust        rust
    go          golang, go.mod, goroutine, gofmt
    typescript  typescript, deno
    javascript  javascript, express.js, node.js, nodejs
    python      python
    java        java, spring boot
    csharp      c#, .net, dotnet, asp.net
    ruby        ruby, rails
    php         php, laravel, symfony
    kotlin      kotlin
    swift       swiftui
    cpp         c++

This alone fixes the reported defect: "Kotlin … Gradle" hits `kotlin` in tier 1
and never reaches `gradle`. Order within the tier stops mattering because no two
entries can match the same token.

### Tier 2 — `_WEAK_SIGNALS`: ordinary English, needs a language context

`go`, `swift`, `ts`, `js`, `rs`, `uv` are whole words with everyday or ambiguous
meanings. They count **only inside a language context** — a phrase that makes the
subject a language:

    before:  written in · rewritten in · ported to · migrated to · in · using · with
    after:   service · module · package · binary · app · application · code ·
             codebase · version · program · library · sdk · backend · api

So "write it in Go" and "a Go service" resolve; "users can go to the next
screen", "approvals go through a review queue" and "a swift response" do not.
This is the class #397 only half-fixed: word boundaries stopped matching *inside*
words, but not tokens that are ordinary words on their own.

### Tier 3 — `_TOOL_SIGNALS` and `_SHARED_TOOLS`

Build tools and ecosystems, reached only when tiers 1 and 2 say nothing.

    rust    cargo, tokio, actix
    python  pytest, fastapi, django, flask
    java    maven
    kotlin  jetpack compose
    cpp     cmake

and separately, tokens that legitimately belong to **several** languages:

    gradle   → java, kotlin, scala, groovy
    android  → java, kotlin

A shared token resolves to **`None`**, not to a guess. `None` already means
"unstated" to `reconcile_language`, which then grounds on the repo language with
no conflict — the honest answer when the spec genuinely has not said. Per the
approved intent, this deliberately does **not** tie-break on the repo language:
#585 requires that a real conflict HALT rather than resolve quietly, and a
repo-preferring tie-break would be a back door around it.

`android` moved here after I first assigned it to kotlin: Android ships both Java
and Kotlin apps, so the platform name alone decides nothing.

## Measured

The candidate resolver over 41 cases — **0 mismatches**. That set is:

- the reported defect (Kotlin + Gradle → `kotlin`, was `java`);
- the two worse cases the intent found (`go` / `swift` prose → `None`, was a
  conflict);
- **every assertion the existing suite already makes**
  (`tests/test_recon_change_mode.py`): `"Build a Rust service with cargo"` → rust,
  `"A FastAPI app"` → python, `"port it to C# please"` → csharp,
  `"a javascript bundler"` → javascript, `"a java service"` → java,
  `"write it in Go"` → go, `"a C++ library with cmake"` → cpp,
  `"an ASP.NET service"` → csharp, and the four that must stay `None`
  (`"the meeting is going ahead"`, `"we trust the caller"`,
  `"a swiftly delivered feature"`, `"just some prose"`);
- adversarial cases I wrote to break my own design: `"The build will go green in
  CI."`, `"The migration will go to production on Friday."`,
  `"Sign in; go to settings."`, `"An Android app built with Gradle."` → all
  `None`; `"An Android app in Kotlin."` → kotlin; `"Ported to TS for type
  safety."` → typescript.

## Alternatives rejected

- **Drop `gradle` from java** (#801 option 1). Fixes the reported case and nothing
  else; `go`/`swift` keep producing false conflicts, and a Gradle-built Java spec
  silently loses its only signal.
- **Give `gradle` to both and tie-break on the repo language** (#801 option 2, and
  what the issue itself recommends). Resolves the report, but a repo-preferring
  tie-break is exactly the silent resolution #585 forbids, and it still leaves the
  English-word class untouched.
- **Score every signal, strongest wins** (#801 option 3). Closest to right, but
  "strength" has to be defined anyway — which is what the tiers do, explicitly and
  readably, instead of via weights nobody can audit.
- **Keep one list, reorder it.** Any order that puts kotlin before java breaks a
  Gradle-built Java spec. Shared tokens have no correct position.
- **Ask an LLM.** A non-deterministic answer feeding a hard gate.

## Risks

- **A Java + Gradle spec with no "java" token now resolves `None`** instead of
  `java`, so it grounds on the repo. That is the intended trade — `gradle` alone
  never proved java — but it is a behaviour change for real specs, so the test
  table records it as an expectation rather than leaving it implicit.
- **`_CTX_BEFORE` contains a bare `in`**, so "… in go …" matches. Adversarial
  probes with punctuation ("Sign in; go to settings.") stay `None`, but a
  sentence running "sign in go to settings" without punctuation would match. Judged
  acceptable: the failure is a false *detection* of the language actually named, not
  of an unrelated one.
- **`scala` and `groovy` appear only as shared-token candidates**, so a Scala spec
  resolves `None` rather than scala. That is already true today (worse: it resolves
  `java`), and adding them is out of scope per the approved intent — but it means
  `None` is doing double duty as "ambiguous" and "unsupported".
- Downstream consumers (`testing_strategy.py`, `tfactory_block.py`, `delta.py`,
  `migration_classifier.py`) read the resolved language. More `None` means more
  repo-grounded resolutions, which is the status quo for an unstated spec.

## Verification

- **Reproduction, before:** `reconcile_language` reports `conflict=True` for the
  Kotlin+Gradle plan and for the `go` / `swift` prose plans against a python repo.
  Already measured; re-run as the before half.
- **After:** those three resolve without conflict — kotlin for the first, and the
  repo language for the other two.
- **Regression:** every existing assertion in `tests/test_recon_change_mode.py`
  passes unchanged. Confirmed against the candidate before writing this.
- **New test:** one parametrised table of the 41 cases above, in the existing test
  file, tagged per tier so a failure says which tier decided.
- **Negative control:** revert each tier one at a time —
  (a) fold `_LANGUAGE_NAMES` back into one ordered list → the Kotlin+Gradle case
  fails again; (b) drop the context requirement from `_WEAK_SIGNALS` → the
  `go`/`swift` prose cases fail; (c) resolve `_SHARED_TOOLS` to its first candidate
  instead of `None` → the Scala/Android cases fail. Each tier must be shown to be
  carrying its own cases.
- Full backend suite via the pre-commit hook.
